#!/usr/bin/env python3
"""Translation tooling for this repo: catalogs, the string check and its
baseline (docs/I18N.md says when to run which).

    tools/i18n.py check            the string check, as the test suite runs it
    tools/i18n.py baseline         drop baseline entries that no longer match
    tools/i18n.py extract [DOMAIN…] refresh the English .ts from the code, then compile
    tools/i18n.py pot [--check]    write the gettext templates (--check: fail if one is stale)
    tools/i18n.py compile [--check] .ts -> .qm (--check: fail if a .qm is stale)
    tools/i18n.py pseudo [DIR]     write the en_XA pseudo-locale catalogs

The same file, apart from CATALOGS, is in every OpenHamShack PyQt6 repo
(Teletext, ATV, DATV, QO-100 NB/Radio, Packet Radio); change it in all of
them or none. Copied into Packet Radio from openhamshack-nb c29fc17 (18/10/2026),
with CATALOGS and POT_DOMAINS this repo's and extract's catalog names taken
from the .ts file name, because this repo's sources live under src/.

Why each piece is the way it is:

- The check is ohs-libs' `shack-i18n-check` (I18N.md section 3), run from a
  pinned wheel in tools/vendor rather than an installed package: the suite
  runs with the system python3 on shack and from a bare clone in CI, and a
  wheel is a zip that Python imports straight off sys.path, so neither needs
  a venv or a network. Update it by copying a new wheel from ohs-libs'
  tools/build.sh and changing WHEEL.
- i18n-pending.txt is the baseline: every literal that was already in the
  code when the check arrived (M1), so the check fails on a *new* literal
  today while M3 converts the old ones. It uses the allow-list's line format
  so an entry that turns out not to be language can move to i18n-allow.txt
  by giving it a real reason. A baseline entry that matches nothing fails the
  check, exactly as a stale allow-list entry does, so the count only falls.
  `baseline` never adds an entry unless told to with --add-new, which is for
  the day the baseline is first written and not for silencing the check.
- .qm files are compiled here in Python instead of by Qt's lrelease, and
  committed. lrelease is not on shack or the build hosts (Debian's PyQt6
  has no Qt tools; pip's PyQt6 has pylupdate6 but no lrelease), the packages
  copy the package directory as it is, so a committed .qm ships with no
  packaging change, and a test proves each .qm matches its .ts. English
  needs its own .qm for plurals alone: without it tr("%n page(s)", "", n)
  shows the source text. The writer produces what lrelease 6.12 writes for
  the same .ts, byte for byte (checked when it was written, 09/10/2026).
- Extraction is pylupdate6, which comes with pip's PyQt6 (not Debian's): see
  docs/I18N.md for the one-line venv. It keeps the English plural forms
  written into the .ts and drops strings that left the code. `extract`
  takes catalog names (`extract shack_packet`) to refresh only those, so two
  people converting different packages do not both rewrite one .ts.
- The gettext templates (`pot`) are written here from an ast scan of the
  `_()`, ngettext() and pgettext() calls, as ohs-libs' tools/extract_pot.py
  does, rather than by `pybabel extract` (I18N.md section 2): Babel is not
  on the build hosts, and the apps use exactly those three spellings. The
  test suite runs `pot --check`, so a sentence cannot reach the code
  without reaching its template, and shack-i18n-check then checks every
  call's msgid against the templates.
"""
from __future__ import annotations

import argparse
import ast
import html
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Each English source catalog and the code pylupdate6 reads for it. One
# catalog: the window is one package (ui/i18n.py loads shack_packet_<lang>).
CATALOGS = [
    {"ts": "src/shack_packet_app/i18n/shack_packet_en.ts", "sources": ["src/shack_packet_app"]},
]

# No gettext domain of the app's own: its words are all in the window (Qt),
# and the engine's over-the-air words are ohs-libs' shack_packet domain.
POT_DOMAINS: list[str] = []

WHEEL = ROOT / "tools" / "vendor" / "shack_i18n-0.1.0-py3-none-any.whl"
BASELINE = ROOT / "i18n-pending.txt"
PSEUDO = "en_XA"


# --------------------------------------------------------------------------
# The check and its baseline

def _shack_i18n():
    if str(WHEEL) not in sys.path:
        sys.path.insert(0, str(WHEEL))
    from shack_i18n import allow, check
    return allow, check


def _findings():
    """(report, baseline entries, problems with the baseline file)."""
    allow, check = _shack_i18n()
    report = check.run(cwd=ROOT)
    entries, problems = allow.load(BASELINE, BASELINE.name)
    return report, entries, problems


def _split(report, entries):
    """Findings the baseline does not cover, and the entries it used.

    Matched on exact path and literal: a baseline entry stands for one
    literal in one file, wherever in that file it moves.
    """
    keyed = {(e.glob, e.literal): e for e in entries}
    left, covered = [], 0
    for f in report.findings:
        e = keyed.get((f.path, f.literal)) if f.literal is not None else None
        if e is None:
            left.append(f)
        else:
            e.used = True
            covered += 1
    return left, covered


def cmd_check(_args) -> int:
    try:
        report, entries, problems = _findings()
    except Exception as err:  # the vendored check's ConfigError, or no wheel
        print(f"i18n check could not run: {err}", file=sys.stderr)
        return 2
    left, covered = _split(report, entries)
    stale = [e for e in entries if not e.used]
    out = sorted(left + report.warnings, key=lambda f: (f.path, f.line, f.col))
    for f in out:
        print(f.render())
    for p in problems:
        print(p.render())
    for e in stale:
        print(f"{BASELINE.name}:{e.line}:1: the baseline entry for {_quote(e.literal)} in {e.glob} "
              f"matched nothing; it has been converted or removed, so run tools/i18n.py baseline")
    failed = len(left) + len(problems) + len(stale)
    print(f"i18n: {failed} finding(s), {len(report.warnings)} warning(s); "
          f"baseline {len(entries)} literal(s) still to convert ({covered} occurrence(s)) in {BASELINE.name}")
    return 1 if failed else 0


def _quote(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


HEADER = """\
# Baseline for shack-i18n-check (docs/I18N.md): user-visible literals that
# were in the code before the check arrived, one per file and literal. M3
# moves each into a catalog and deletes its line; the check fails on any
# literal not listed here or in i18n-allow.txt, and on a line here that no
# longer matches. Rewrite with tools/i18n.py baseline, never by hand-adding.
# path | "literal" | pending
"""


def cmd_baseline(args) -> int:
    report, entries, problems = _findings()
    if problems:
        for p in problems:
            print(p.render())
        return 1
    left, _ = _split(report, entries)
    keep = {(e.glob, e.literal) for e in entries if e.used}
    new = [f for f in left if f.literal is not None]
    if new and not args.add_new:
        for f in new:
            print(f.render())
        print(f"{len(new)} new literal(s) above are not in the baseline. Put them in the catalog "
              f"(or in i18n-allow.txt with a reason); --add-new is only for writing the first baseline.")
    if args.add_new:
        keep |= {(f.path, f.literal) for f in new}
    removed = len(entries) - len([e for e in entries if e.used])
    lines = [f"{path} | {_quote(lit)} | pending" for path, lit in sorted(keep)]
    BASELINE.write_text(HEADER + "".join(line + "\n" for line in lines), encoding="utf-8")
    print(f"{BASELINE.name}: {len(lines)} literal(s); removed {removed}")
    return 1 if new and not args.add_new else 0


# --------------------------------------------------------------------------
# .ts -> .qm

QM_MAGIC = bytes([0x3C, 0xB8, 0x64, 0x18, 0xCA, 0xEF, 0x9C, 0x95, 0xCD, 0x21, 0x1C, 0xBF, 0x60, 0xA1, 0xBD, 0xDD])
_HASHES, _MESSAGES, _NUMERUS, _LANGUAGE = 0x42, 0x69, 0x88, 0xA7
_END, _TRANSLATION, _SOURCE, _CONTEXT, _COMMENT = 1, 3, 6, 7, 8
# Qt's plural rules, as lrelease encodes them (qtranslator.cpp's Q_EQ etc.):
# "n == 1 is form 0, anything else form 1". Only English is needed while
# the beta ships English; a language with other rules is compiled by
# lrelease or gets its rule added here, never guessed.
_RULES = {"en": bytes([0x01, 0x01])}


def _elf_hash(data: bytes) -> int:
    h = 0
    for c in data:
        h = ((h << 4) + c) & 0xFFFFFFFF
        g = h & 0xF0000000
        if g:
            h ^= g >> 24
        h &= ~g & 0xFFFFFFFF
    return h or 1


def _blob(b: bytes) -> bytes:
    return struct.pack(">I", len(b)) + b


def read_ts(path: Path) -> tuple[str, list[tuple[str, str, str, list[str]]]]:
    """(language, [(context, source, comment, translations)]) as lrelease
    would keep them: no obsolete or vanished entries, none untranslated."""
    root = ET.parse(path).getroot()
    out = []
    for ctx in root.iter("context"):
        name = ctx.findtext("name") or ""
        for msg in ctx.iter("message"):
            tr = msg.find("translation")
            if tr is None or tr.get("type") in ("obsolete", "vanished"):
                continue
            if msg.get("numerus") == "yes":
                forms = [n.text or "" for n in tr.iter("numerusform")]
            else:
                forms = [tr.text or ""]
            if not forms or not forms[0]:
                continue  # untranslated: Qt shows the source text
            out.append((name, msg.findtext("source") or "", msg.findtext("comment") or "", forms))
    return root.get("language", ""), out


def qm_bytes(language: str, messages: list[tuple[str, str, str, list[str]]]) -> bytes:
    """A .qm as lrelease writes it in its default (non-compressed) mode."""
    rules = _RULES.get(language.replace("-", "_").split("_")[0])
    if rules is None:
        raise ValueError(f"no plural rule for language {language!r}; compile it with lrelease")
    # lrelease's Releaser::insert: a disambiguation comment is dropped unless
    # an earlier message (in .ts order) already holds that context and source
    # without one. Qt's lookup retries without the comment, so the first of a
    # pair answers comment-less lookups and the rest keep theirs.
    table: dict[tuple[bytes, bytes, bytes], list[str]] = {}
    for c, s, m, t in messages:
        key = (c.encode(), s.encode(), b"")
        table[key if key not in table else (key[0], key[1], m.encode())] = t
    msgs = sorted(table.items())
    body, offsets = bytearray(), []
    for (ctx, src, comment), forms in msgs:
        offsets.append((_elf_hash(src + comment), len(body)))
        for f in forms:
            body += bytes([_TRANSLATION]) + _blob(f.encode("utf-16-be"))
        body += bytes([_COMMENT]) + _blob(comment) + bytes([_SOURCE]) + _blob(src)
        body += bytes([_CONTEXT]) + _blob(ctx) + bytes([_END])
    hashes = b"".join(struct.pack(">II", h, o) for h, o in sorted(offsets))
    out = bytearray(QM_MAGIC)
    if language:
        out += bytes([_LANGUAGE]) + _blob(language.encode())
    out += bytes([_HASHES]) + _blob(hashes) + bytes([_MESSAGES]) + _blob(bytes(body))
    out += bytes([_NUMERUS]) + _blob(rules)
    return bytes(out)


def qm_for(ts: Path) -> bytes:
    return qm_bytes(*read_ts(ts))


def cmd_compile(args) -> int:
    stale = 0
    for c in CATALOGS:
        ts = ROOT / c["ts"]
        qm = ts.with_suffix(".qm")
        want = qm_for(ts)
        if args.check:
            if not qm.is_file() or qm.read_bytes() != want:
                print(f"{qm.relative_to(ROOT)} does not match {ts.name}; run tools/i18n.py compile")
                stale += 1
        else:
            qm.write_bytes(want)
            print(f"wrote {qm.relative_to(ROOT)}")
    return 1 if stale else 0


# --------------------------------------------------------------------------
# Extraction

def _pylupdate6() -> str | None:
    found = os.environ.get("PYLUPDATE6") or shutil.which("pylupdate6")
    if not found:
        beside = Path(sys.executable).with_name("pylupdate6")
        found = str(beside) if beside.is_file() else None
    return found


def _domain(c: dict) -> str:
    """A catalog's name on the command line: its .ts without _en.ts."""
    return Path(c["ts"]).name.removesuffix("_en.ts")


def cmd_extract(args) -> int:
    domains = getattr(args, "domains", None) or []
    unknown = [d for d in domains if d not in [_domain(c) for c in CATALOGS]]
    if unknown:
        print(f"no catalog for {', '.join(unknown)}", file=sys.stderr)
        return 2
    tool = _pylupdate6()
    if tool is None:
        print("pylupdate6 not found. It comes with pip's PyQt6, not Debian's; see docs/I18N.md "
              "(or set PYLUPDATE6 to its path).", file=sys.stderr)
        return 2
    unfilled = 0
    for c in CATALOGS:
        if domains and _domain(c) not in domains:
            continue
        ts = ROOT / c["ts"]
        ts.parent.mkdir(parents=True, exist_ok=True)
        if not ts.exists():
            # Say it is English before pylupdate6 first writes it: that is
            # where it learns a plural has two forms, and where lrelease and
            # the check learn this is the source catalog.
            ts.write_text('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE TS>\n'
                          '<TS version="2.1" language="en" sourcelanguage="en">\n</TS>\n', encoding="utf-8")
        subprocess.run([tool, "--no-obsolete", "--no-summary", "--ts", str(ts), *[str(ROOT / s) for s in c["sources"]]],
                       check=True, cwd=ROOT)
        unfilled += _fill_plurals(ts)
        print(f"updated {c['ts']}")
    if unfilled:
        print(f"{unfilled} English plural(s) need their two forms written into the .ts by hand "
              f"(the check names them)", file=sys.stderr)
    return cmd_compile(argparse.Namespace(check=False))


_PLURAL_MARK = re.compile(r"(\w)\((\w+)\)")


def plural_forms(source: str) -> list[str] | None:
    """English singular and plural from the "(s)" convention:
    "Saved %n page(s)" -> ["Saved %n page", "Saved %n pages"]. None when the
    source does not follow it ("%n entry/entries" is written by hand)."""
    if not _PLURAL_MARK.search(source):
        return None
    return [_PLURAL_MARK.sub(r"\1", source), _PLURAL_MARK.sub(r"\1\2", source)]


def _fill_plurals(ts: Path) -> int:
    """Write the English forms of new plurals; returns how many it could not.

    Done on the text, not through ElementTree, so the rest of the file stays
    exactly as pylupdate6 wrote it and diffs show only real changes."""
    text = ts.read_text(encoding="utf-8")
    unfilled = 0

    def fill(m: re.Match) -> str:
        nonlocal unfilled
        block = m.group(0)
        if re.search(r"<numerusform>[^<]", block):
            return block  # already written, perhaps by hand
        source = html.unescape(re.search(r"<source>(.*?)</source>", block, re.S).group(1))
        forms = plural_forms(source)
        if forms is None:
            unfilled += 1
            return block
        body = "".join(f"\n            <numerusform>{html.escape(f, quote=False)}</numerusform>" for f in forms)
        return re.sub(r"<translation[^>]*?(/>|>.*?</translation>)",
                      lambda _: f"<translation>{body}\n        </translation>", block, count=1, flags=re.S)

    text = re.sub(r'<message numerus="yes">.*?</message>', fill, text, flags=re.S)
    ts.write_text(text, encoding="utf-8")
    return unfilled


# --------------------------------------------------------------------------
# gettext templates

_GETTEXT_CALLS = {"_": 1, "ngettext": 2, "pgettext": 2}


def _str(node) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def pot_entries(package: Path) -> dict:
    """{(context, msgid): {"plural": str | None, "where": [path:line]}} for
    every _("..."), ngettext("...", "...", n) and pgettext("ctx", "...")
    with literal arguments in the package. A call with a computed argument
    is not a template entry (and shack-i18n-check reports it)."""
    entries: dict = {}
    for p in sorted(package.rglob("*.py")):
        rel = p.relative_to(ROOT).as_posix()
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"), rel)):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in _GETTEXT_CALLS and len(node.args) >= _GETTEXT_CALLS[node.func.id]):
                continue
            a = node.args
            if node.func.id == "_":
                key, plural = (None, _str(a[0])), None
            elif node.func.id == "ngettext":
                key, plural = (None, _str(a[0])), _str(a[1])
            else:
                key, plural = (_str(a[0]), _str(a[1])), None
            if key[1] is None:
                continue
            e = entries.setdefault(key, {"plural": None, "where": []})
            e["plural"] = e["plural"] or plural
            e["where"].append(f"{rel}:{node.lineno}")
    return entries


def render_pot(domain: str, entries: dict) -> str:
    q = _quote
    out = [f"# English template for the {domain} gettext domain (docs/I18N.md).",
           "# Written by tools/i18n.py pot; do not edit.",
           'msgid ""', 'msgstr ""', '"Content-Type: text/plain; charset=UTF-8\\n"', '"Language: en\\n"', ""]
    for (ctx, msgid) in sorted(entries, key=lambda k: (k[1], k[0] or "")):
        e = entries[(ctx, msgid)]
        out += [f"#: {w}" for w in e["where"]]
        if "{" in msgid or (e["plural"] and "{" in e["plural"]):
            out.append("#, python-brace-format")  # named placeholders, filled by str.format after translation
        if ctx is not None:
            out.append(f"msgctxt {q(ctx)}")
        out.append(f"msgid {q(msgid)}")
        if e["plural"] is not None:
            out += [f"msgid_plural {q(e['plural'])}", 'msgstr[0] ""', 'msgstr[1] ""']
        else:
            out.append('msgstr ""')
        out.append("")
    return "\n".join(out)


def pot_path(domain: str) -> Path:
    return ROOT / domain / "i18n" / f"{domain}.pot"


def cmd_pot(args) -> int:
    stale = 0
    for domain in POT_DOMAINS:
        path, text = pot_path(domain), render_pot(domain, pot_entries(ROOT / domain))
        if args.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != text:
                print(f"{path.relative_to(ROOT)} is out of date; run tools/i18n.py pot")
                stale += 1
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            print(f"wrote {path.relative_to(ROOT)}")
    return 1 if stale else 0


# --------------------------------------------------------------------------
# Pseudo-locale (I18N.md section 5.2)

_ACCENT = dict(zip("AEIOUYCNaeiouycn", "ÅÉÎÕÛÝÇÑåéîõûýçñ"))


def pseudo_text(text: str) -> str:
    """English made obviously not English, 40% longer and bracketed, with
    every placeholder (%n, %1, {name}) and & shortcut marker left working,
    so clipping and untranslated strings show at a glance."""
    out, i = [], 0
    while i < len(text):
        ch = text[i]
        if ch == "%" and i + 1 < len(text) and (text[i + 1].isdigit() or text[i + 1] in "nL"):
            j = i + 2
            while j < len(text) and text[j].isdigit():
                j += 1
            out.append(text[i:j])
            i = j
        elif ch == "{" and "}" in text[i:]:
            j = text.index("}", i) + 1
            out.append(text[i:j])
            i = j
        else:
            out.append(_ACCENT.get(ch, ch))
            i += 1
    letters = sum(1 for ch in text if unicodedata.category(ch).startswith("L"))
    return "[" + "".join(out) + "~" * max(1, round(letters * 0.4)) + "]"


def pseudo_ts(ts: Path) -> ET.ElementTree:
    tree = ET.parse(ts)
    root = tree.getroot()
    root.set("language", PSEUDO)
    for msg in root.iter("message"):
        tr = msg.find("translation")
        if tr is None or tr.get("type") in ("obsolete", "vanished"):
            continue
        src = msg.findtext("source") or ""
        if msg.get("numerus") == "yes":
            english = [n.text or src for n in tr.iter("numerusform")] or [src]
            for n in list(tr):
                tr.remove(n)
            for form in (english + [english[-1]])[:2]:
                ET.SubElement(tr, "numerusform").text = pseudo_text(form)
        else:
            tr.text = pseudo_text(tr.text or src)
        tr.attrib.pop("type", None)
    return tree


def write_pseudo(out_dir: Path | None = None) -> list[Path]:
    """Each catalog's en_XA files, in out_dir or else beside its own .ts,
    which is where the loader looks (an app with several catalogs needs
    each one in its own package's folder)."""
    written = []
    for c in CATALOGS:
        ts = ROOT / c["ts"]
        out_dir_here = out_dir or ts.parent
        out_dir_here.mkdir(parents=True, exist_ok=True)
        name = ts.name.replace("_en.ts", f"_{PSEUDO}")
        tree = pseudo_ts(ts)
        tree.write(out_dir_here / f"{name}.ts", encoding="utf-8", xml_declaration=True)
        qm = out_dir_here / f"{name}.qm"
        qm.write_bytes(qm_for(out_dir_here / f"{name}.ts"))
        written.append(qm)
    return written


def cmd_pseudo(args) -> int:
    for qm in write_pseudo(Path(args.dir) if args.dir else None):
        print(f"wrote {qm}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="tools/i18n.py", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="the string check with the baseline (what the tests run)")
    b = sub.add_parser("baseline", help="drop baseline entries that no longer match")
    b.add_argument("--add-new", action="store_true", help="also add every current finding (first baseline only)")
    e = sub.add_parser("extract", help="refresh the English .ts files with pylupdate6, then compile")
    e.add_argument("domains", nargs="*", help="only these catalogs (shack_packet); default all")
    pt = sub.add_parser("pot", help="write the gettext templates from the _()/ngettext()/pgettext() calls")
    pt.add_argument("--check", action="store_true", help="fail if a committed template is out of date")
    c = sub.add_parser("compile", help="compile .ts to .qm")
    c.add_argument("--check", action="store_true", help="fail if a committed .qm does not match its .ts")
    ps = sub.add_parser("pseudo", help="write en_XA pseudo-locale catalogs (not committed)")
    ps.add_argument("dir", nargs="?", help="where to write them (default: beside each English catalog)")
    args = p.parse_args(argv)
    return {"check": cmd_check, "baseline": cmd_baseline, "extract": cmd_extract,
            "compile": cmd_compile, "pseudo": cmd_pseudo, "pot": cmd_pot}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
