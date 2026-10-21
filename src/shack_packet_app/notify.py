"""What is worth telling the operator about when it arrives, and the
sidebar's unread counts (ui/notifier.py draws them; notifysound.py makes
the sound). Asked for by Brian on 19/10/2026: in the GM0OTB test a
message sat unseen behind another page.

Three things notify, each its own switch in Settings › Notifications:

- **an APRS message to this station** (Messages page). The library has
  already checked the addressee - this station's call *with its SSID*, or
  a call it also answers to - and dropped a repeat of a message number it
  has taken (aprs_station.py: a retry, or a message heard again through
  a digipeater). Here a message whose source is this station itself is
  dropped too: our own message heard back.
- **a line from a station in a Connect/Chat session** (Connect / Chat),
  including a node conference and, since 0.3.0, a line a member says in
  the room this station hosts. Only complete lines with something in
  them; a prompt still arriving without its line end waits for the rest.
- **a message left in the mailbox for the operator** (Mailbox): not mail
  left by one caller for another to collect.

Since 0.3.0 the Mailbox also tells of mail this station *sent*: a
message delivered, or given up on after its last round (kind
"mail_status", its own switch, on the Mailbox page), and Winlink email
collected from a gateway notifies as mail left for the operator does.

Nothing else this station sends is ever a notice: these events are only made
for what came in. A notice interrupts (the desktop notification and the
sound) only when the operator cannot already see it: the window is not the
active one, or its page is not the one showing. The unread count is kept
whatever the switches say, and cleared when the page is looked at.
"""
from __future__ import annotations

import collections
import re
from dataclasses import dataclass

#: How much of a message the notification shows.
PREVIEW_CHARS = 80

#: Each kind's page (window.PAGES), and its switch in NotificationSettings.
PAGE = {"messages": "messages", "chat": "chat", "mailbox": "mailbox", "mail_status": "mailbox"}


@dataclass(frozen=True)
class Notice:
    kind: str            # "messages", "chat" or "mailbox" (and the page it belongs on)
    sender: str
    text: str            # at most PREVIEW_CHARS, one line
    #: For "mail_status": "delivered" or "failed".
    what: str = ""

    @property
    def page(self) -> str:
        return PAGE[self.kind]


def preview(text: str, limit: int = PREVIEW_CHARS) -> str:
    """`text` on one line, cut at a word to at most `limit` characters
    with an ellipsis when cut."""
    one = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(one) <= limit:
        return one
    cut = one[:limit - 1]
    space = cut.rfind(" ")
    if space > limit // 2:
        cut = cut[:space]
    return cut.rstrip() + "…"


def _same_station(a: str, b: str) -> bool:
    """Call and SSID equal, "-0" being no SSID."""
    def norm(x: str) -> str:
        x = str(x or "").strip().upper()
        return x[:-2] if x.endswith("-0") else x
    return norm(a) == norm(b)


def notice_for(event, own: str | None) -> Notice | None:
    """The notice for a Controller AppEvent, or None. `own`: this
    station's address (call-SSID), to drop our own messages heard back."""
    kind, reason, d = event.kind, event.reason, event.detail or {}
    if kind == "aprs" and reason == "message":
        sender = str(d.get("peer", ""))
        if own and _same_station(sender, own):
            return None
        return Notice("messages", sender, preview(d.get("text", "")))
    if kind == "chat" and reason == "data":
        if not d.get("lines") or not str(d.get("text", "")).strip():
            return None
        return Notice("chat", str(d.get("remote", "")), preview(d["text"]))
    if kind == "room" and reason in ("say", "me"):
        # A line in the room this station hosts (room.py): a chat line
        # like any other, on the same switch and the same page.
        return Notice("chat", str(d.get("remote", "")), preview(d.get("text", "")))
    if kind == "mail" and reason == "mail-received":
        sender = str(d.get("sender", ""))
        if not d.get("for_me") or (own and _same_station(sender, own)):
            return None
        subject, body = str(d.get("subject", "")).strip(), str(d.get("body", "")).strip()
        return Notice("mailbox", sender, preview(f"{subject}: {body}" if subject and body else subject or body))
    if kind == "mail" and reason == "winlink-received":
        return Notice("mailbox", str(d.get("sender", "")), preview(d.get("subject", "")))
    if kind == "mail" and reason in ("mail-delivered", "mail-failed"):
        return Notice("mail_status", str(d.get("to", "")), preview(d.get("subject", "")),
                      "delivered" if reason == "mail-delivered" else "failed")
    return None


def wanted(notice: Notice, ns) -> bool:
    """Whether settings.NotificationSettings `ns` wants this kind shown."""
    return bool(ns.enabled) and bool(getattr(ns, notice.kind, True))


def interrupts(notice: Notice, *, window_active: bool, page: str) -> bool:
    """Whether the operator would miss it without a notification."""
    return not (window_active and page == notice.page)


class Unread:
    """Unread counts by page."""

    def __init__(self):
        self.counts: collections.Counter = collections.Counter()

    def add(self, page: str) -> None:
        self.counts[page] += 1

    def seen(self, page: str) -> bool:
        """Clear `page`; whether it had any."""
        return self.counts.pop(page, 0) > 0

    def count(self, page: str) -> int:
        return self.counts.get(page, 0)
