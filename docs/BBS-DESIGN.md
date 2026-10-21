# The controller's BBS: design (Packet Radio 0.3.0, workstream C)

Status: design and first build, 20/10/2026, branch `feature/app-030-c`
(this repo) and `feature/libs-030-c` (ohs-libs). Nothing deployed, no radio
keyed: everything below was tested on the simulator only. **[guess]** marks
what is not verified.

## 1. What it is

A packet BBS, an always-on conference room and a mail store that run on
the OpenHamShack controller (ohs-box, Brian's M720q), on one radio the
controller owns. Brian's decisions:

- **D1** (18/10): the desktop app runs the modem for general use; "the
  controller will get a dedicated BBS etc and reserve the radio it's using
  so it can't be used by the program".
- **D2** (20/10): the controller BBS is in 0.3.0, not a later release.

Family rules it keeps: the controller owns the hardware (the broker lends
the radio); no server inside the desktop app (the BBS is a store app on
the controller, never part of the desktop window); one client per
platform (the sysop page is the store app's own web page, and the desktop
and Remote only *see* the BBS); never key a radio without Brian (tests are
simulation only, and transmit is off until a sysop arms it).

## 2. Where it runs

| Piece | Where | Why there |
|---|---|---|
| BBS commands, mail store, conference room, FBB forwarding, ID beacon | ohs-libs `shack_packet` (`bbs.py`, `bbsstore.py`, `conference.py`, `fbb.py`, `idbeacon.py`) | "Shared components, one of each": the desktop's conference host (workstream B) and its store-and-forward client (workstream E) use the same room and the same forwarding code. Bytes in, bytes out, no radio, like `mailbox.py`. |
| The store app (server, broker client, sysop page) | this repo, `src/shack_packet_box/` and `store/packet/` | REPO-LAYOUT §4.1: one app codebase, two forms. The desktop app never imports `shack_packet_box`; the store image never runs the Qt window. |
| Radio links | `shack_packet_app.links.make_link` (reused, no Qt) | One way to open each radio; the box opens network radios only (§4). |

Store app: module id **`shack-packet`**, kind `packet` (REPO-LAYOUT §4's
`shack-<kind>`), route `/shack-packet/`, `python3 -m shack_packet_box`
in a rootless container on port 8080, data on the module's `/data` volume.

```
 RF ── radio (IC-9700 LAN / Pluto / simulator)
          │  lent by the broker (claim, reserved, rx→tx on arm)
   ┌──────┴──────────────────────── shack-packet container ─────────────┐
   │ Engine (shack_packet) ── BrokeredLink: onair before key, off after │
   │   ├─ Bbs on CALL-1     ── BbsStore (SQLite, /data/bbs.db)          │
   │   │    └─ FBB forwarding (answering) / Forwarder (calling, partners)│
   │   ├─ Room on CALL-2 (and the BBS's C command)                      │
   │   └─ IdBeacon                                                      │
   │ HTTP: sysop page + api/ (proxy secret, levels)                     │
   └────────────────────────────────────────────────────────────────────┘
```

## 3. Users and their commands

Callsign-only users: the AX.25 source call is the identity, as on every
packet BBS. No passwords on RF (nothing secret may be sent in the clear,
and a UK licence forbids encrypting to hide meaning), so nothing on the
BBS is more private than "addressed to you": a station that forges a call
can read that call's mail, as on GB7 BPQ boxes today. The sysop can bar a
call. Mail belongs to the call without SSID (G0ABC-9 reads G0ABC's).

FBB/BPQ-compatible commands (case ignored, CR/LF/CRLF):

| Command | Does |
|---|---|
| `L` | messages new since your last `L` (personal to you, and bulletins) |
| `LM` | your personal mail |
| `LL n` | the last n messages |
| `LB` | bulletins; `L> CALL`/`L< CALL`/`L@ AREA` filter by to/from/at |
| `R n` / `RM` | read one / read all your unread personal mail |
| `K n` / `KM` | kill one of yours / all your read personal mail |
| `S CALL [@ BBS]`, `SP`, `SB TOPIC [@ AREA]` | send personal / bulletin; subject, then text ended by `/EX` or Ctrl-Z on its own line; `/ABORT` gives up |
| `N name`, `NH bbs`, `NQ qth` | your name, home BBS, location |
| `I` | the BBS's information text; `I CALL` what the BBS knows of a station |
| `C` / `CONF` / `CHAT` | join the conference room (leave with `/quit` back to the BBS) |
| `H` / `?` / `HELP` | the commands |
| `B` / `BYE` / `Q` | disconnect |
| `[SID]` as the first line | switches to FBB forwarding (§6) |

Per-user limits (settings): message size (default 8 kB), messages a call
may leave a day (default 20), messages kept for one call (default 100),
idle time before disconnect (default 10 min), concurrent sessions (default
4 in all). Bulletins expire (default 30 days), personal mail (default 90
days, read mail 14 days); killed messages are gone at the next sweep.

## 4. The radio and the broker

**Which radio.** One per BBS, chosen by the sysop: a network radio the
broker lends (`ic9700-1`/`ic705-1` over the LAN, `pluto-1`), or the
simulator (default, so installing the app keys nothing). USB sound-card
radios need a PTT output, and PTT outputs are broker-only
(CONTROLLER-CONTRACT §7.2), so they wait for the broker to lend audio
devices. Default channel 144.950 MHz 1200 baud AFSK, BBS SSID -1, room
-2, all configurable (Brian, 20/10/2026); the broker's band-plan check
judges every over.

**Reservation (D1).** While the BBS runs it holds a broker claim on its
radio, `requester: auto`, `reason: "BBS GM4BRI-1 on 144.950 MHz"`, and
heartbeats it every 2 s; mode `rx` until a sysop arms transmit, then `tx`.
The broker's device row then names the holder, and the desktop app
refuses to open a radio the controller lists as held (`ohsclient.blocks`),
so "it can't be used by the program" holds with today's broker. The
desktop shows it as **"reserved by Packet Radio BBS: not used from this
computer"** (ohsclient treats a `shack-packet` holder as a reservation
until the broker sends `reserved`, §8 decision 2).

- A person's hand-over request (`release_request`, stop false) is answered
  `409 {"reserved": true}`: a BBS is not a receiver that can step aside
  for a minute. An admin's **Stop all transmitting** (`stop: true`) or
  **Stop transmitting and hand over** is obeyed at once: unkey, release.
- Stopping the app, or the sysop's "Release the radio", unkeys, ends the
  claim (`DELETE`) and closes the link; on_shutdown does it within the
  module's stop timeout (GUIDE §5 "A stop that overruns...").
- A lost claim (broker restart, 404/410 on heartbeat) is re-claimed with
  `reclaim: true` and backoff, receive only, and the BBS does not key
  until the claim is back (GUIDE §5 "A platform update drops every
  hardware claim").

**Transmit.** The engine keys only through `BrokeredLink`: before each
over it asks the broker `onair {on, freqHz, bandwidthHz, mode: "packet",
chain}`; a refusal is a link refusal (the frames are dropped, logged, the
link layer retries later); after the over, `onair {on:false}`. The broker
needs an **acting identity with the transmit level** (CONTRACT §6.2), and
a BBS answers callers with nobody at the controller. So:

- The sysop **arms** transmit on the sysop page (needs `transmit`). The
  app keeps the arming request's `X-OpenHamShack-Device` (`dev_…` or
  `session:…`) and sends it as `X-OpenHamShack-Acting-Device` on every
  `onair`. The broker re-checks that person's level at every over, so
  revoking the device, signing out, or downgrading the user stops the BBS
  transmitting at the next over. The arm also has an expiry (default 24
  h, renewed by arming again; "until disarmed" allowed) and is lost on
  restart (Brian, 20/10/2026: kept for 0.3.0). The page and the
  audit say "transmitting for alice (browser)".
- Brian's presence rule: the arm is the sysop's say-so. It is never set by
  default, by a test, or by an install, and this workstream never set it.
- UK unattended operation: a BBS answering while nobody is at the radio
  is unattended operation, which a UK Full licence permits (confirmed by
  GM4BRI 20/10/2026). Close-down is the broker's Stop all transmitting, the sysop
  page's Disarm, and the app's stop.

**Identification.** Every frame carries the station's call in its AX.25
address. On top, `IdBeacon` sends a plain-text UI frame `ID` ("GM4BRI-1
BBS, GM4BRI-2 conference, 144.950 MHz") after any over when it has not
identified for 15 minutes, and never while the BBS is silent (BPQ's
IDINTERVAL behaviour); 15 minutes is the UK licence's maximum
identification interval (confirmed by GM4BRI 20/10/2026). An optional information beacon (`MAIL`
destination, "N new bulletins") is off by default, minimum 10 minutes.

## 5. The conference room

`shack_packet.conference.Room`, the same semantics as the desktop host
(workstream B is asked to host with this class rather than write a
second): every line a member types is sent to every other member as
`CALL: text`; join and leave are announced (`*** G0ABC joined`, `***
G0ABC left`); commands `/who`, `/quit` (or `/q`, `/bye`), `/me action`
(`* G0ABC waves`), `/topic [text]`, `/help`; the last 10 lines are shown
on joining; members idle 30 min are dropped; at most 20 members; a member
may not send more than 10 lines in 10 s (excess dropped, told once). The
room is always on while the app runs: stations reach it by connecting to
the room SSID (default -2) or by `C` from the BBS. The sysop page shows
members and lines and lets the sysop speak (as `SYSOP`), set the topic
and remove a member.

## 6. Store-and-forward

The protocol is **FBB ASCII forwarding** (`F` in the SID, proposals `FB`,
answers `FS`, messages ended by Ctrl-Z, `FF`/`FQ`), the one BPQMail and
FBB both speak, specified exactly in ohs-libs
`python/shack_packet/FORWARDING.md`. Two uses:

1. **OpenHamShack stations (workstream E)**: a desktop station connects to
   the BBS, sends `[OHS-<v>-FHM$]`, proposes the mail it has to send
   (deliver) and accepts what the BBS proposes for its call (collect).
2. **Other BBSs** (forwarding partners, later the UK GB7 network): the
   same exchange, both directions, with partners listed in the settings
   (call, connect path, routes). The box calls a partner when it has mail
   for it, at most every `FWD_INTERVAL_MIN` (default 30).

Compressed forwarding (B1, LZHUF) is not in 0.3.0: the SID offers `F`
only, so a B1/B2 partner falls back to ASCII. Compression is not
encryption and is allowed; it is a later speed-up (§8 decision 4).

## 7. Storage, sysop page, settings, upgrade

**Storage.** `/data/bbs.db` (SQLite, WAL): `messages` (number, type P/B,
from, to, at, subject, body, bid, size, created, expires, state
new/read/forwarded/killed, origin), `deliveries` (message, partner or
call, state), `users` (call, name, home BBS, QTH, first/last seen,
connects, sent today, barred), `seen` (call, last listed number). The
room keeps nothing on disk. Logs: a ring of the last 2000 lines in memory
for the page, and stdout for the journal. Backed up by the volume
(`backup: true`).

**Sysop page** (the app's base URL, through the platform's front door,
so its sign-in and levels): Overview (radio, claim, ARMED/receive-only,
on air, sessions now), Users (list, bar/unbar), Messages (list, read,
kill), Room (members, last lines, topic, say, remove), Forwarding
(partners, queue, last exchange), Log. Levels: `view` reads; `control`
kills, bars, edits the room; `transmit` arms. Every change is logged with
who did it.

**Settings** (Dock360 module env, one line each; a setting cannot hold a
line break): `BBS_CALLSIGN`, `BBS_SSID` (1), `ROOM_SSID` (2, 0 = no
direct room), `RADIO_KIND` (simulator | ic-9700 | ic-705 | pluto),
`RADIO_DEVICE` (broker id), `RADIO_PASSWORD` (Icom LAN), `FREQUENCY_HZ`,
the limits in §3, `BBS_INFO` (the I text, `|` for a new line),
`FWD_PARTNERS` (comma-separated `CALL[via DIGI DIGI]`), `FWD_INTERVAL_MIN`,
`ID_TEXT`.

**Upgrade path.** The database carries `PRAGMA user_version`; each schema
change is a numbered migration in `bbsstore.py`, run at open, and a
database newer than the code is refused (the app stays receive-only and
says so) rather than downgraded. Settings keep their names; a new one has
a default. The store app's version is SemVer of its own (0.3.0 with the
desktop). Mail survives updates (volume) and restores from backup.

## 8. Decisions (Brian, 20/10/2026: each as recommended)

1. **Unattended transmit:** for 0.3.0 the BBS transmits on behalf of the
   sysop who armed it (their device or browser session as acting
   identity, re-checked by the broker every over, with an expiry).
   *Recommended.* Later, a platform "unattended service" switch on the
   Hardware page (CONTRACT §6.2 extension) so the arm does not die with a
   browser session. Alternative: no transmit until the platform has that
   switch (BBS receive-only on ohs-box).
2. **"Reserved" in the broker:** built on our own branch of the edition,
   dock360 `feature/ohs-edition-030-c` (not merged; note for the
   platform's owner in its `docs/openhamshack-030-c-review.md`): `reserve:
   true` on a claim, device row `reserved: {app, name, claimId, reason,
   since}`, person requests and hand-overs refused `device_reserved`; the
   same branch adds the kind `packet`. The desktop reads `reserved` when
   present and otherwise treats a `shack-packet` holder as a reservation.
3. **Default channel and SSIDs:** 144.950 MHz, BBS -1, room -2.
   *Recommended* unless a local GB7 BBS uses them; confirm the channel.
4. **B1 compressed forwarding:** later (0.3.x). *Recommended*: ASCII FBB
   forwarding is what every partner accepts; LZHUF needs test vectors from
   a real BPQ before it is worth shipping.
5. **Who may deliver mail by forwarding:** any station may deliver
   personal mail for local users and collect its own; only listed
   partners may deliver bulletins or mail for elsewhere. *Recommended*
   (keeps a stranger from relaying through the box).

## 9. The change checklist (GUIDE §3)

1. Contract: FORWARDING.md (new, ohs-libs); CONTROLLER-CONTRACT gains
   reservations as a proposal (openhamshack `feature/guide-030-c`).
2. Server: new store app `shack-packet` (this repo `store/packet`).
3. Fake: n/a (the fakebox has no packet); tests use a fake broker.
4. Android/TV: n/a for 0.3.0 (no packet kind in Remote).
5. Apple: n/a.
6. Desktop: ohsclient shows a BBS holder as reserved; workstream E's
   forwarding client uses `shack_packet.fbb`; B's room host uses
   `shack_packet.conference`.
7. Hardware: broker claim, reserved, released on stop, re-claimed after a
   broker restart.
8. Transmit: BrokeredLink + sysop arm + broker licence check + the radio
   links' own safety cores; never keyed in testing.
9. Release: a new store package (staged by `tools/build-store.sh`,
   signed by openhamshack-os `tools/package-app.sh` as every store app
   is) and an ohs-libs re-vendor; the desktop's 0.3.0 carries the libs.
   **The platform must first learn the kind `packet`**: the edition's
   manifest validator refuses it (`SHACK_KINDS`, dock360
   feature/ohs-edition `manifest.py:885`); dock360
   `feature/ohs-edition-030-c` adds it and reservations, for the platform
   owner to review and merge before the release. Who cuts it: the 0.3.0 integration phase.

## 10. As built and tested (20/10/2026)

- ohs-libs `feature/libs-030-c`: `fbb.py`, `bbsstore.py`, `bbs.py`,
  `conference.py`, `idbeacon.py`, `FORWARDING.md`; tests `test_bbs.py`
  (two simulated stations through their own engines: mail, privacy,
  limits, barring, idle, the room both ways, flood and history, a station
  delivering and collecting by FBB forwarding, refusals, two BBSs
  forwarding both ways, the ID beacon's timing, the store's expiry,
  numbering and schema) and `test_fbb.py` (SIDs as BPQ/Winlink/FBB send
  them, line ends, blocks over five each way, deferral, a lost link, a
  protocol error, the idle time-out).
- This repo `feature/app-030-c`: `src/shack_packet_box`, `store/packet`,
  tests `test_box_station.py` and `test_box_server.py` (the BBS on the
  simulator against a fake broker: reservation and release, the desktop
  reading the row as reserved, armed overs each on and off the air for
  the arming device, nothing keyed unarmed or when the broker refuses,
  the arm's expiry, hand-over refused and admin stop obeyed, a broker
  restart re-claimed receive-only; the HTTP side's proxy secret, levels,
  platform routes, page, catalog and template).
- `tools/bbs-test.sh all` runs both suites (35 tests, about 4 s).
- The image builds (`tools/build-store.sh --image`), self-checks, serves
  the page on the simulator and stops in 0.2 s on SIGTERM (podman on
  shack, 20/10).
- **Untested:** any real radio, the real broker (only a fake that answers
  as CONTROLLER-CONTRACT §7 says), forwarding with a real FBB or BPQMail
  (the checksum and R: line formats are marked guesses), the sysop page in
  a browser (served and its API tested; not opened), a store install on a
  controller.
