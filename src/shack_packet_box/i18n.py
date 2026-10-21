"""The store app's own words, through stdlib gettext: domain
`shack-packet-box`, English when no catalog is installed (I18N.md §2;
the beta ships English only). Text the BBS sends over the air is the
library's (shack_packet.i18n); this is what the sysop page and the logs
of the server say."""
from __future__ import annotations

import gettext
from pathlib import Path

DOMAIN = "shack-packet-box"
_t = gettext.translation(DOMAIN, Path(__file__).resolve().parent / "locale", fallback=True)


def _(message: str) -> str:
    return _t.gettext(message)
