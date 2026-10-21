"""What a Connect/Chat session's text means, for the helpers beside the
terminal: whether the far end is a node, a BBS or a conference, the
prompt it is waiting at, the NODES and ROUTES rows it printed, the
message list and the message the operator asked for, and who is in a
conference. ohs-libs' shack_packet.terminal reads the shapes; this keeps
the state of one session.

**The terminal stays raw.** Everything arrives in the session's lines
exactly as before; this only reads along. A helper that misreads costs a
missing row, never a changed or hidden line, and every helper button
types the same command the operator could type themselves (L, R 12,
K 12, CHAT), visibly, in the session.

**The kind** is guessed from what the far end says ("ALIAS:CALL}" lines
are a node; "Connected to BBS", a "de CALL>" prompt or a [BPQ-...] SID
are a BBS; "Connected to Chat" or a "Here:" list after the operator's
conference command, or a room's "*** ... here:" welcome, are a
conference), and the operator can set it in the
session's own menu; once set by hand it is not guessed again.
"""
from __future__ import annotations

import re

from shack_packet import terminal

_SID = re.compile(r"^\[[A-Z0-9]+-[\w.-]*\$?\]$", re.IGNORECASE)
KINDS = ("chat", "node", "bbs", "conference")


class SessionReader:
    def __init__(self, own: str = ""):
        self.own = own
        self.kind = "chat"
        self.kind_by_hand = False
        self.listing = terminal.ListingReader()
        self.participants = terminal.Participants(own)
        self.prompt = ""
        self.prompt_kind = ""
        #: A helper's command waiting for its answer: "list" or "read".
        self.collecting = ""
        self._collected: list[str] = []
        self.mail: list[terminal.MailItem] = []
        self.mail_rev = 0
        self.reading = 0                        # the number asked for with R
        self.read_text = ""
        self.read_rev = 0
        self.joined_conference = False
        self.participants_rev = 0

    def set_kind(self, kind: str, by_hand: bool = False) -> None:
        if kind not in KINDS:
            return
        if by_hand:
            self.kind_by_hand = True
        elif self.kind_by_hand:
            return
        self.kind = kind

    # -- what arrives -----------------------------------------------------------

    def line(self, text: str) -> list:
        """One received line. Returns NODES/ROUTES entries it held."""
        self.prompt, self.prompt_kind = text, terminal.prompt_kind(text)
        stripped = text.strip()
        nl = terminal.node_line(text)
        if nl is not None:
            low = nl.text.lower()
            if "connected to bbs" in low:
                self.set_kind("bbs")
            elif "connected to chat" in low or "connected to conf" in low:
                self.set_kind("conference")
                self.joined_conference = True
            elif "returned to node" in low:
                self.set_kind("node")
                self.joined_conference = False
            elif self.kind == "chat":
                self.set_kind("node")
        elif _SID.match(stripped) or self.prompt_kind == "bbs":
            if self.kind in ("chat", "node"):
                self.set_kind("bbs")
        elif self.kind == "chat" and stripped.startswith("***") and terminal.here_line(text) is not None:
            # A room's welcome ("*** Welcome to the GM4BRI-4 conference. 2
            # here: ..."): another OpenHamShack station's or controller's room.
            self.set_kind("conference")
            self.joined_conference = True
        if self.kind == "conference" or self.joined_conference:
            before = list(self.participants.calls)
            ev = self.participants.feed(text)
            if ev is not None and ev.kind == "list" and self.kind != "conference":
                self.set_kind("conference")
            if self.participants.calls != before:
                self.participants_rev += 1
            if "left the conference" in stripped.lower():
                self.joined_conference = False
        if self.collecting:
            if self.prompt_kind == "bbs":
                self._finish()
            else:
                self._collected.append(text)
        return self.listing.feed(text)

    def partial(self, text: str) -> None:
        """Text the far end sent without a line end yet (a prompt)."""
        if text:
            self.prompt, self.prompt_kind = text, terminal.prompt_kind(text)
            if self.prompt_kind == "bbs" and self.kind in ("chat", "node"):
                self.set_kind("bbs")
            if self.collecting and self.prompt_kind == "bbs":
                self._finish()

    # -- the helpers' commands ----------------------------------------------------------

    def expect(self, what: str, number: int = 0) -> None:
        """A helper is about to type a command: read its answer as `what`."""
        self.collecting, self._collected = what, []
        if what in ("read", "kill"):
            self.reading = number

    def joining(self) -> None:
        self.joined_conference = True
        self.participants = terminal.Participants(self.own)
        self.participants_rev += 1

    def _finish(self) -> None:
        what, lines = self.collecting, self._collected
        self.collecting, self._collected = "", []
        if what == "list":
            self.mail = terminal.parse_mail_list(lines)
            self.mail_rev += 1
        elif what == "read":
            self.read_text = "\n".join(lines).strip("\n")
            self.read_rev += 1
        elif what == "kill":
            self.mail = [m for m in self.mail if m.number != self.reading]
            self.mail_rev += 1
