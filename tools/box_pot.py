#!/usr/bin/env python3
"""Write (or with --check, verify) the English gettext template of the
store app's own words, src/shack_packet_box/locale/shack-packet-box.pot.

tools/i18n.py is the same file in every OpenHamShack Qt repo and keeps its
POT_DOMAINS beside each repo's root package, which the store app is not;
rather than change the shared file for one repo, this uses its scanner and
writer on the store app's package, so the template is written exactly as
every other one is.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "src" / "shack_packet_box"
DOMAIN = "shack-packet-box"
POT = PACKAGE / "locale" / f"{DOMAIN}.pot"


def render() -> str:
    spec = importlib.util.spec_from_file_location("i18n_tool", ROOT / "tools" / "i18n.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    return tool.render_pot(DOMAIN, tool.pot_entries(PACKAGE))


def main(argv: list[str]) -> int:
    text = render()
    if "--check" in argv:
        if not POT.is_file() or POT.read_text(encoding="utf-8") != text:
            print(f"{POT.relative_to(ROOT)} is out of date; run tools/box_pot.py")
            return 1
        return 0
    POT.parent.mkdir(parents=True, exist_ok=True)
    POT.write_text(text, encoding="utf-8")
    print(f"wrote {POT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
