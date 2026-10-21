"""Connect scripts: the steps an operator takes every time to reach a BBS
or a node several hops away, written down once and run with one click.

    # GB7EDN's BBS, through the Lothians node
    connect GB7LOT via MB7UC
    wait }
    send C GB7EDN
    wait Connected to
    send BBS
    wait de GB7EDN>

One step a line; `#` starts a comment. `connect CALL [via A,B]` must come
first and only once. `wait TEXT` holds the script until TEXT turns up in
what the far end sends (case ignored; `a|b` waits for either), for at most
`timeout` seconds (TIMEOUT_S unless a `timeout N` line says otherwise),
after which the script stops and says why; nothing is sent after a wait
that did not come true. `send TEXT` sends one line (CR-ended, as typed);
a `send` with nothing after it sends a bare CR, which a paging question
or a node's "press return" needs.

**What runs it.** Only the operator, by choosing the script: nothing here
starts by itself, retries, or sends anything a script does not say
(Brian's rule for this feature: never auto-send anything except a user's
script). The steps after `connect` begin once the link is up, so a
script started disarmed waits like any connection and sends nothing; and
every line goes through the station's channel access and transmit switch.
The operator can stop a script at any time, and typing in the session
does not disturb it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

#: How long a `wait` holds before the script gives up: a node two hops
#: away on a busy 1200-baud channel can take a minute to answer.
TIMEOUT_S = 120.0
#: The most a script may be: a script is a few lines, not a file to send.
MAX_STEPS = 100
VERBS = ("connect", "wait", "send", "timeout")


class ScriptError(ValueError):
    """A script that cannot run; `line` is its 1-based line number."""

    def __init__(self, line: int, key: str, detail: str = ""):
        super().__init__(f"line {line}: {key} {detail}".strip())
        self.line = line
        self.key = key
        self.detail = detail


@dataclass(frozen=True)
class Step:
    verb: str
    arg: str
    line: int = 0


@dataclass
class Script:
    name: str
    text: str

    def steps(self) -> list[Step]:
        return parse(self.text)


def parse(text: str) -> list[Step]:
    steps: list[Step] = []
    for n, raw in enumerate(str(text or "").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        verb, _sep, arg = line.partition(" ")
        verb = verb.lower()
        if verb not in VERBS:
            raise ScriptError(n, "unknown-step", verb)
        if verb == "connect":
            if steps:
                raise ScriptError(n, "connect-not-first")
            if not arg.strip():
                raise ScriptError(n, "connect-needs-call")
        elif not steps:
            raise ScriptError(n, "connect-not-first")
        if verb == "wait" and not arg.strip():
            raise ScriptError(n, "wait-needs-text")
        if verb == "timeout":
            try:
                if float(arg) <= 0:
                    raise ValueError
            except ValueError:
                raise ScriptError(n, "bad-timeout", arg) from None
        # `send` keeps its text as typed after the one space (leading
        # spaces can matter to a BBS); the rest are trimmed.
        steps.append(Step(verb, raw.lstrip()[len(verb) + 1:] if verb == "send" else arg.strip(), n))
        if len(steps) > MAX_STEPS:
            raise ScriptError(n, "too-long")
    if not steps:
        raise ScriptError(0, "empty")
    return steps


def target(steps: list[Step]) -> tuple[str, str]:
    """(call, via) of a script's connect step; via comma-separated."""
    arg = steps[0].arg.upper()
    m = re.match(r"^(\S+)(?:\s+VIA\s+(.+))?$", arg)
    if m is None:
        return arg, ""
    via = ",".join(p for p in re.split(r"[\s,]+", m.group(2) or "") if p)
    return m.group(1), via


@dataclass
class ScriptRun:
    """One run of a script on one session. `send(line)` is the session's
    way out; the controller feeds `heard(text)` with everything received
    and calls `poll(now)` as time passes."""
    name: str
    steps: list[Step]
    send: object
    state: str = "waiting-link"          # waiting-link | running | done | failed | stopped
    reason: str = ""
    at: int = 1                          # the next step (0 is connect)
    timeout_s: float = TIMEOUT_S
    _heard: str = ""
    _deadline: float | None = None
    log: list = field(default_factory=list)

    @property
    def waiting_for(self) -> str:
        if self.state == "running" and self.at < len(self.steps) and self.steps[self.at].verb == "wait":
            return self.steps[self.at].arg
        return ""

    def start(self, now: float) -> None:
        """The link is up: run until the first wait."""
        if self.state != "waiting-link":
            return
        self.state = "running"
        self._advance(now)

    def heard(self, text: str, now: float) -> None:
        if self.state != "running" or not self.waiting_for:
            return
        # Kept to a few kB: a wait matches recent text, and a prompt split
        # across two frames still matches once both halves are in.
        self._heard = (self._heard + text)[-4096:]
        wanted = [w.strip().lower() for w in self.waiting_for.split("|") if w.strip()]
        if any(w in self._heard.lower() for w in wanted):
            self._heard = ""
            self.at += 1
            self._advance(now)

    def poll(self, now: float) -> None:
        if self.state == "running" and self._deadline is not None and now >= self._deadline:
            self.state, self.reason = "failed", "script-timeout"
            self._deadline = None

    def next_deadline(self) -> float | None:
        return self._deadline if self.state == "running" else None

    def stop(self, reason: str = "script-stopped") -> None:
        if self.state in ("waiting-link", "running"):
            self.state, self.reason = "stopped", reason
            self._deadline = None

    def _advance(self, now: float) -> None:
        self._deadline = None
        while self.at < len(self.steps):
            step = self.steps[self.at]
            if step.verb == "timeout":
                self.timeout_s = float(step.arg)
            elif step.verb == "send":
                self.send(step.arg)
                self.log.append(step.arg)
            elif step.verb == "wait":
                self._deadline = now + self.timeout_s
                return
            self.at += 1
        self.state, self.reason = "done", "script-done"


def send_mail_steps(to: str, subject: str, body: str, bulletin: bool = False) -> list[Step]:
    """The dialogue a BPQMail or FBB BBS holds to take a message (SP/SB,
    its title question, the text and /EX), as a script's steps after the
    connect: sent only because the operator pressed Send in the mail
    helper, and stopped like a script if the BBS answers otherwise."""
    lines = str(body or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    steps = [Step("connect", "-"), Step("send", f"{'SB' if bulletin else 'SP'} {to.strip().upper()}"),
             Step("wait", "title|subject"), Step("send", subject.strip() or "-"),
             Step("wait", "/ex|ctrl|text")]
    steps += [Step("send", line) for line in lines]
    steps.append(Step("send", "/EX"))
    return steps
