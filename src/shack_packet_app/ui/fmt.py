"""Numbers, frequencies and times in the operator's locale.

Frequencies always in MHz with the locale's decimal separator, through
ohs-libs' shack_i18n.fmt (pinned by the family's format vectors, so the
Remote apps write the same digits); three decimals, or four for the
12.5 kHz channels (144.9375) that three would round to a different
channel. Times through QLocale, in UTC for the logs and lists (what packet
stations quote to each other) with the local time in tooltips.
"""
from __future__ import annotations

from PyQt6.QtCore import QDateTime, QLocale, QTimeZone
from shack_i18n import fmt as shack_fmt


def _tag() -> str:
    return QLocale().name()


def mhz(hz: float) -> str:
    decimals = 4 if int(round(hz)) % 1000 else 3
    return shack_fmt.frequency(int(round(hz)), _tag(), unit="MHz", decimals=decimals)


def khz(hz: float) -> str:
    """A width in whole kHz (a channel plan's span, an SDR's capture)."""
    return shack_fmt.frequency(int(round(hz)), _tag(), unit="kHz", decimals=0)


def number(x: float, decimals: int = 0) -> str:
    return QLocale().toString(float(x), "f", decimals)


def _dt(wall: float) -> QDateTime:
    return QDateTime.fromMSecsSinceEpoch(int(wall * 1000), QTimeZone.utc())


def utc_time(wall: float | None) -> str:
    """HH:MM:SS UTC, the locale's own time separator."""
    if wall is None:
        return ""
    return QLocale().toString(_dt(wall).time(), "HH:mm:ss")


def utc_date(wall: float | None) -> str:
    """The UTC date, the locale's short form."""
    if wall is None:
        return ""
    return QLocale().toString(_dt(wall).date(), QLocale.FormatType.ShortFormat)


def utc_datetime(wall: float | None) -> str:
    if wall is None:
        return ""
    return QLocale().toString(_dt(wall), QLocale.FormatType.ShortFormat)


def local_datetime(wall: float | None) -> str:
    if wall is None:
        return ""
    return QLocale().toString(QDateTime.fromMSecsSinceEpoch(int(wall * 1000)), QLocale.FormatType.LongFormat)


def minutes_seconds(seconds: float) -> str:
    s = max(0, int(round(seconds)))
    return f"{s // 60}:{s % 60:02d}"
