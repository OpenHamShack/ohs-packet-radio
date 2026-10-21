"""Which language the window speaks (OpenHamShack I18N.md; openhamshack-nb's
ohscore/i18n is the family's pattern and this is its single-catalog form).

British English is the source: every user-visible string is written in
English inside `self.tr()` or `QCoreApplication.translate()`, and the
catalogs in `shack_packet_app/i18n/` (`shack_packet_<lang>.ts`, compiled to
`.qm` by tools/i18n.py) carry translations. The English catalog is
installed whatever the language, because it holds the English plural forms
(`tr("%n station(s)", "", n)` would otherwise show the source text) and is
the fallback for a string another catalog lacks.

The choice: `SHACK_LOCALE` if set (`de`, `pt_BR`, `en_XA` for the
pseudo-locale), else the desktop's preferred languages, first one with a
catalog; English last.

The engine's over-the-air words (the mailbox's prompts) are gettext, in
the library's own domain; the library and shack_bandplan pick their
language from the environment, which `install()` sets to the same choice.
"""
from __future__ import annotations

import os
from pathlib import Path

CATALOG_DIR = Path(__file__).resolve().parent.parent / "i18n"
DOMAIN = "shack_packet"

_installed: list = []


def candidates(tag: str) -> list[str]:
    tag = tag.split(".")[0].split("@")[0].replace("-", "_").strip()
    if not tag or tag in ("C", "POSIX"):
        return []
    parts = tag.split("_")
    return ["_".join(parts[:i]) for i in range(len(parts), 0, -1)]


def preferred() -> list[str]:
    from PyQt6.QtCore import QLocale
    override = os.environ.get("SHACK_LOCALE", "").strip()
    if override:
        return [override]
    return list(QLocale.system().uiLanguages())


def choose(tags) -> str:
    for tag in tags:
        for cand in candidates(tag):
            if (CATALOG_DIR / f"{DOMAIN}_{cand}.qm").is_file():
                return cand
    return "en"


def install(app=None, locale: str | None = None) -> str:
    from PyQt6.QtCore import QCoreApplication, QTranslator
    app = app or QCoreApplication.instance()
    for t in _installed:
        app.removeTranslator(t)
    _installed.clear()
    chosen = locale or choose(preferred())
    for lang in ("en", chosen) if chosen != "en" else ("en",):
        t = QTranslator()
        if t.load(str(CATALOG_DIR / f"{DOMAIN}_{lang}.qm")):
            app.installTranslator(t)
            _installed.append(t)
    if chosen != "en":
        os.environ.setdefault("LANGUAGE", chosen)
    return chosen
