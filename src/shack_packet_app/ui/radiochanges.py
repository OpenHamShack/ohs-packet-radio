""""What the app changed on your radio": the radio link's data set-up
report (shack_rig.datasetup, by way of Controller.radio_setup) as rich text,
for the setup check's listening page and Station › What the app changed on
the radio.

In the order an operator wants it: anything to check first (the warnings),
what was changed and to what, what to set by hand, what was only read, and
what was put back - at close, and after a session that died.
"""
from __future__ import annotations

import html

from PyQt6.QtCore import QCoreApplication

from . import texts


def _row(line: dict) -> str:
    name = html.escape(texts.setup_setting(line.get("key", "")))
    before = html.escape(texts.setup_value(line.get("before")))
    after = html.escape(texts.setup_value(line.get("after")))
    status = html.escape(texts.setup_status(line.get("status", "")))
    if before and after:
        value = f"{before} → {after}"
    else:
        value = before or after
    note = line.get("note") or ""
    tail = f"<br/><i>{html.escape(note)}</i>" if note else ""
    return f"<tr><td>{name}</td><td>{value}</td><td>{status}{tail}</td></tr>"


def _table(lines: list[dict]) -> str:
    return "<table cellspacing='4'>" + "".join(_row(ln) for ln in lines) + "</table>"


def html_report(setup: dict) -> str:
    """The rich text for Controller.radio_setup; empty when there is nothing
    to say (no rig link has opened)."""
    report = list(setup.get("report") or [])
    restored = list(setup.get("restored") or [])
    recovered = list(setup.get("recovered") or [])
    warnings = list(setup.get("warnings") or [])
    if not (report or restored or recovered or warnings):
        return ""
    out = []

    def section(title: str, lines: list[dict]):
        if lines:
            out.append(f"<p><b>{html.escape(title)}</b></p>" + _table(lines))

    if warnings:
        out.append(f"<p><b>{html.escape(QCoreApplication.translate('RadioChanges', 'Check'))}</b></p><ul>"
                   + "".join(f"<li>{html.escape(texts.setup_warning(k, d))}</li>" for k, d in warnings) + "</ul>")
    section(QCoreApplication.translate("RadioChanges", "Put back first, from the last session"), recovered)
    section(QCoreApplication.translate("RadioChanges", "Changed for packet (put back when the app closes)"),
            [ln for ln in report if ln.get("status") in ("set", "set-unrestorable", "failed")])
    section(QCoreApplication.translate("RadioChanges", "To set on the radio yourself"),
            [ln for ln in report if ln.get("status") == "manual"])
    section(QCoreApplication.translate("RadioChanges", "Read and left as they were"),
            [ln for ln in report if ln.get("status") in ("already", "report", "unavailable", "unread")])
    section(QCoreApplication.translate("RadioChanges", "Put back when the radio was closed"), restored)
    return "".join(out)
