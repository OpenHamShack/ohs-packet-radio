# OpenHamShack Packet Radio: release notes

## 0.3.2 (21/10/2026)

Three things in it have not yet been tried on the air: the quicker 9600
transfers, the TM-V71, and following the radio's dial. Each says so below.

- **9600 file transfers are much quicker.** 0.3.0 and 0.3.1 sent up to 63
  frames before waiting for an acknowledgement at every fast mode. At
  9600 that is two 10-second transmissions back to back, and the other
  station, after 15 seconds of a busy channel, took it for a stuck squelch
  and transmitted over the second one: half of every batch was lost and
  had to be sent again. Two changes, each of which fixes it alone:
  - **The window is now chosen per mode** (Settings › Fast modes,
    "Best for the mode", on unless you set a window by hand): what one
    transmission carries - 31 frames at 9600, 62 at 19 200, 63 above.
    With link adaptation on, the slowest mode's.
  - **A station never talks over the station it is connected to.** A busy
    channel in which your link partner was heard sending you frames is
    waited out to its end (at most 45 seconds); the 15-second limit stays
    for anyone else and for a squelch stuck open.
- In the simulator, 100 KB at 9600 now takes about 113 s (178 s in
  0.3.1); 1200, 19 200 and 38 400 baud are unchanged. Not yet tried on the
  air.
- **Fixed: the app now follows the radio's own dial** (Brian, 0.3.1 on
  an IC-9700 over the LAN: turning the VFO left the app on 144.925). With
  any radio the app controls - IC-9700 and IC-705 by LAN or USB, FTX-1,
  TM-V71 - turn the VFO and, once it has stayed put for a second, that
  is the station's channel: the status bar, the channel box on the control
  strip (the preset's name, or "Other frequency" with the frequency), the
  transfer estimates, the scope's markers and the log all follow, and it
  is saved, so the next start tunes there. The app no longer tunes the
  radio back when it reconnects or the station is rebuilt.
  - **What you choose on the radio stays when the app closes.** The app
    still puts back what it changed to set the radio up for packet, but a
    setting you changed yourself on the radio while the app ran (the
    frequency, or anything else) is left as you set it, and "What the app
    changed on the radio" says "kept". A channel chosen in the app is
    still the app's, and the radio goes back to where it was found.
  - **Transmit, while you turn the dial:** nothing is sent until the
    radio has settled on its new frequency (a transmission under way when
    the dial moves is cut). Then anything meant for the old channel is
    cancelled, not sent on the new one: frames waiting to go, connections
    (dropped without a word on the air: the station you were talking to
    is not on the new channel) and APRS messages still waiting for their
    acknowledgement. The status bar says how many of each. If you were
    armed, the new channel is checked as Arm checks it: where packet is
    allowed you stay armed and the status bar says so; where it is not
    (the satellite segment, a band your licence does not cover) transmit
    is disarmed, with the reason.
  - **Turn the mode knob off FM** (to SSB, say) and transmit is disarmed
    at once and held until the radio is back in FM (FM-D, DATA-FM).
  - A satellite pass with Doppler on moves the VFOs itself and is left to
    do so.
  - Tested against pretend IC-9700, FTX-1 and TM-V71 radios only: **not
    yet tried with a real radio** (tester guide section 5, "Turning the
    radio's own dial").
- **Fixed: the app could close by itself while you read mail** if a
  message arrived at the same moment (two parts of the program saving the
  mailbox at once).
- **New radio: the Kenwood TM-V71 (E and A).** Its PC port controls the
  radio and a sound-card interface on its DATA jack carries the packet:
  a Digirig (PTT by RTS), a SignaLink (its own VOX) or a CM108 board
  (PTT on a GPIO pin, new in this version). **1200 AFSK and 9600 G3RUH**:
  choose the speed on the setup check's radio page or the control strip.
  Opening the radio picks the band (A or B) already on 2 m or 70 cm, puts
  it in VFO, tunes it, sets FM simplex with no tone, and points the DATA
  jack at it at the right speed (menus 517 and 518); everything is put
  back when the app closes, and the app never keys the radio over its PC
  port. The control strip has the radio's power (H, M, L), its squelch
  (read only) and "channel busy". Find my radio recognises a TM-V71 on a
  serial port and pairs the interface beside it. Tester guide section 5.
  Written from the radio's published and reverse-engineered command sets
  and tested against a pretend TM-V71 only: **not yet tried with a real
  one** (section 7 lists what we need to hear).

## 0.3.1 (20/10/2026)

Everything in 0.3.0 is here, plus one thing Brian asked for: **how long
a file will take, at every speed**.

- **The Send dialog has a table of the time at every mode** (tester
  guide section 14): AFSK 1200, G3RUH 9600, 19 200 and 38 400, GFSK
  76 800 and 153 600, 4-FSK 38 400 and 76 800, and 23 cm DD, each as
  "about 2 min 10 s" on a good link and with retries. It is worked out
  for the bytes that would really go: after compression where it
  applies, and for a picture after it is made smaller for the size you
  choose (change the size or the protocol and the table changes with
  it). Your station's mode is highlighted; modes your radio cannot send
  are greyed and say why ("not available on IC-9700"; DD only on an
  IC-9700 with 23 cm DD turned on; the fast modes only on a Pluto). DD
  says "estimate, unmeasured": nobody has run it yet.
- **The estimates are not raw baud.** They follow a transfer the way
  the program runs it: AX.25 headers, flags and bit stuffing (and FX.25
  if it is on), the window and packet length, TXDELAY and the turn round
  for every over and its acknowledgement, and the file protocol's own
  framing and messages. They were checked against real runs: 5142 bytes
  in 42.8 s at 1200 baud between GM4BRI and GM0OTB, and 10 KB at 9600,
  19 200 and 38 400 between two Plutos; and against the program's own
  simulated channel at every mode.
- **Measured beats estimated.** After a file transfer or a link test
  with a station, its estimates say "measured with GM0OTB: …" and use
  what that path really did.
- The picture sizes in the Send dialog show the time at your mode the
  same way, and the Files page shows the table for the file you select,
  with how long a finished one took and how long a running one has left.

## 0.3.0 (20/10/2026)

Everything in 0.2.3 is here, plus the largest set of new things yet: the
link test by callsign, Find my radio and a control strip always on
screen; UK nodes, BBSs and conferences; files and pictures; mail between
stations and Winlink email; APRS extras; and an IC-9700 page with its
spectrum, adaptive power, satellites and 23 cm DD. Section numbers are
the tester guide's.

**All of 0.3.0 was tested on the simulator and against pretend radios,
nodes, BBSs and gateways only. None of it has been on the air yet.**
Please treat every new feature as a first try, and tell us what a real
station, node or gateway does differently (section 6).

### New: link test by callsign, Find my radio, the control strip

- **Link test by callsign, both ways by itself** (tester guide section
  11). Type the other station's callsign and press Start; the two
  stations find each other through openhamshack.com and run A to B, then
  B to A. No relay address, session or roles to agree. Whoever's call
  comes first alphabetically sends first; the sender arms as usual and
  the receiver never transmits. A Pluto is asked to move its antenna
  between halves; other radios need nothing. Results side by side, a
  summary, and Save report. The 0.2.1 controller relay is under Advanced.
- **Find my radio** (section 5) at the top of the setup check's radio
  page and in Settings › Radio: finds an Icom or Yaesu on the serial
  ports, an Icom on the network, Plutos and a controller's radios, fills
  every field in, matches the radio's sound devices, says what to set on
  the radio, and tests the connection read-only.
- **The control strip** (section 5) under the pages: channel and mode,
  RF power, RF gain and squelch (an Icom) or TX and RX gain (a Pluto),
  live signal meter and, on transmit, the IC-9700's Po, SWR and ALC, with
  a warning when SWR jumps. Changes apply at once and are put back when
  the app closes. The control pad's knobs turn power and squelch.
- **The retro notification voice is now a recording**: "You've got
  mail!", "You've got a message!" and "New chat!" in Cori's voice (a
  free, public-domain voice made with Piper), after the chime. Your
  computer's own voice is still there as a choice.

### New: UK nodes and BBSs, conferences, callsign lookup and the log

Tester guide section 13.

- **Connect / Chat knows what it is talking to.** Beside the terminal, a
  node gets buttons for Nodes, Routes, BBS, Info and Join conference; a
  BBS (BPQMail, FBB) gets its message list (List, Mine), a reader, Kill
  and New message; a conference gets the list of who is in it. Every
  button types the command it stands for into the session, where you see
  it; the terminal itself is unchanged and you can still type anything.
  What is at the other end is guessed from what it sends, and you can set
  it yourself in the session's own list.
- **Continue and Stop listing** at a BBS's paging question
  ("<A>bort, <CR> Continue..>", "More (Y/N)?").
- **Capture** a session to a text file.
- **Connect scripts** (Scripts › Edit scripts…): write once how you reach
  a BBS through nodes (connect, wait for a prompt, send C GB7XXX, send
  BBS) and run it with one click. A script only runs when you choose it,
  starts after the link is up, and stops if what it waits for does not
  come.
- **Nodes page**: every NET/ROM node heard broadcasting on the air (the
  radio and the SDR monitor), and every node a node you are connected to
  lists when you type N or R, with its quality and the best neighbour to
  go through. Double-click to connect: directly, or through the
  neighbour with a C command.
- **Host a room** (Connect / Chat › Room): stations connect to your call
  with SSID 4 (you can choose) and everything one says goes to all, with
  their callsign in front, join and leave notices, /who, /me, /topic and
  /quit, a room name and topic, a member limit and an idle timeout - the
  same room the OpenHamShack controller runs. The room answers only while
  you have it open and transmit is armed.
- **Tabs show unread lines**: a session or the room behind another tab
  shows its count in its title until you look at it; room lines notify
  like chat lines.
- **Callsign lookup** (Station › Callsign lookup, free HamQTH account):
  name, QTH, locator, distance and bearing from your locator, on hover in
  the Monitor, chat tabs, the Nodes page, the room and the log. Kept on
  this computer, so it works offline for calls already looked up. The
  password is kept with the radio password, never in settings or
  diagnostics.
- **Log page**: contacts from connected sessions and stays in your room,
  logged by hand ("Log contact") or by themselves when a session ends
  (switches on the Log page, all off to begin with); filter, then export
  what is shown as ADIF 3.1 or CSV. Mode is PKT with the speed in the
  comment (ADIF has no packet submodes), and no made-up signal reports.
- **The simulator's GB7SIM is now a pretend BPQ node** with a BBS (paged
  mail), a chat conference, NODES broadcasts, and C GB7SJ to a second
  pretend node, so all of the above can be tried with no radio.

### New: files and pictures over a connected session

Tester guide section 14.

- **Send a file or picture to a station you are connected to**: drop it
  on the chat tab (anywhere on the Connect / Chat page), or press **Send
  file…** beside the tabs or on the new **Files** page. It goes over the
  same connection as the chat, so it works through nodes, at any speed
  the station runs - and a fast mode on a Pluto moves a picture in
  seconds rather than minutes.
- **Pictures are made smaller first if you like**: Large (1600 pixels),
  Medium (1024, the default), Small (640), Tiny (320) or the original
  file, each shown with its size and roughly how long it takes on the air
  at your current mode. The smaller copies are new JPEGs, so they leave
  behind the camera details and location a phone puts in a photo;
  "Original" sends the file exactly as it is.
- **The other station is asked first**: who it is from, the name, size
  and type, and where it will be saved. Nothing is written until it is
  accepted, offers over a size you choose (50 MB to start) are declined
  for you, and received files are never opened or run by the app - the
  Files page shows them, with a thumbnail for a picture, and **Show
  folder** opens their folder.
- **Progress at both ends**, with the speed, and **Cancel** from either.
- **Resume**: if the link drops, connect again and the file carries on
  from where it stopped (by itself between two OpenHamShack stations, or
  with **Resume**). The whole file is checked (SHA-256) when it arrives,
  so a resumed file is as good as one sent in one go.
- **Compression**: between OpenHamShack stations files and long lines of
  text are compressed (DEFLATE, a published method anyone can undo - not
  encryption) when that makes them smaller; pictures and archives, which
  are compressed already, are sent as they are. Short chat lines go as
  plain text.
- **YAPP** for other packet software (WinPack, FBB, BPQ terminals and
  others), with YAPP-C's checksums and resume. Choose it in the Send
  dialog, or as the default on the Files page. Received YAPP files are
  asked about the same way.
- **7PLUS** for files posted through BBSs: on the Files page, encode a
  file into parts to post as messages, or put one back together from
  saved messages, a capture file or pasted text - in any order; it says
  which parts are still missing.
- **G4SIMF on the simulator**: a pretend station to try all of this on.
  Send it a file and it says it got it; type PIC, TXT or YAPP and it
  sends you one.
- **Encrypted files are refused** before anything is sent (a PGP or age
  file, an encrypted ZIP): a UK licence does not allow encrypted
  messages.

Not yet tried on the air or against other packet programs: YAPP is
written from F6FBB's published code and tested against a station played
from its bytes; 7PLUS is checked against 7PLUS 2.25 itself.

### New: mail between stations, Winlink email, and APRS extras

Tester guide sections 15 and 16.

- **The Mailbox is a mail client.** *Packet mail* tab: an Inbox (what
  callers left, what other stations delivered, and what you collected
  from your BBS, in one list) and *Outbox and sent*, where each message
  you wrote says where it is: queued, sending, delivered (and through
  whom), or failed (and why). New, Reply, Send now, Collect now, Try
  again.
- **Store-and-forward between stations.** Write to `CALL` or
  `CALL@BBS`; the message waits in the outbox until transmit is armed,
  then goes by itself: to the addressee's own OpenHamShack station (its
  mailbox now takes mail from other stations' mail clients), to a
  forwarding partner that routes their BBS, or to your home BBS (an
  OpenHamShack controller's BBS or a UK GB7). Not answered: tried again
  after 10 minutes, half an hour, an hour... and given up after six
  rounds. **Collect** your own mail from the home BBS when you ask, or
  every so many minutes. Plain FBB forwarding, as BPQMail and FBB
  speak it (no compression yet). Settings › Mail.
- **Notifications** for mail you sent: delivered, or could not be
  delivered (its own switch in Settings › Notifications).
- **Winlink email** (Mailbox › Winlink email): to and from callsigns and
  internet addresses through an RMS packet gateway, with attachments
  (20 kB a message unless you change it). Write any time, connect to a
  gateway when you are on the air. Needs a free Winlink account for your
  callsign; the password stays on this computer - the gateway sends a
  challenge and only the answer goes on the air. Messages are compressed
  the way Winlink always does (LZHUF), which is not encryption: anyone can
  read them. **Gateways are a list you type** (Settings › Mail): Winlink's
  own gateway list needs a key it issues only to registered programs.
- **GPS** (Settings › GPS): a serial GPS (NMEA), gpsd on Linux, or a
  position you type in, instead of your locator. With *SmartBeaconing*
  the beacon goes often when you move fast or turn and seldom when you
  are still (never more than once a minute).
- **iGate** (Settings › iGate): what you hear goes to APRS-IS (connecting
  out to rotate.aprs2.net; nothing listens on your computer). Optionally,
  messages from APRS-IS to stations heard directly here in the last half
  hour go out on the air - nothing else from the internet, at most six a
  minute, and only while transmit is armed. The passcode is worked out
  from your callsign.
- **Weather** (Settings › Weather): a Peet Bros ULTIMETER (data logger
  mode), a Davis Vantage (LOOP), or a JSON/CSV file another program
  writes (WeeWX, Cumulus, a WS-2300 program) becomes an APRS weather
  report every 10 minutes (never under 5); stale readings are not sent.
- **Telemetry**: frames heard, sent, digipeated and gated since the last
  report, every 20 minutes (never under 10), with the PARM/UNIT/EQNS/BITS
  that say what they are.
- **Objects and items**: right-click the APRS page's map to place one (a
  net, a repeater, an event); **My objects…** lists, moves and kills them.
  Each is sent every 30 minutes (never under 10); a killed one three
  times, so maps take it away. Yours are drawn in the accent colour.
- **The simulator** has M0SIM (a station whose mailbox takes mail),
  GB7SBB-1 (a home BBS) and GB7RMS-10 (a pretend Winlink gateway that
  takes any password and has a welcome email waiting), so all of this can
  be tried with no radio.

### New: the IC-9700 page - spectrum, power, satellites, 23 cm DD

Tester guide section 17. Everything here was tested against a pretend
IC-9700 only; nothing of it has been on the air yet.

- **Spectrum and waterfall** from the IC-9700's own scope over the
  network: span, reference and speed, the packet channels marked, busy
  ones shaded, and a click on a channel tunes the radio there (the station
  is disarmed first). The radio sends its scope only while the page is on
  screen. Not over a USB cable at 19200 baud (too slow); the page says so.
- **Adaptive power** (off until you switch it on): the lowest RF power
  that still works. It starts at your cap, steps down while the other
  station keeps acknowledging, and back up as soon as frames need
  repeating; never above the cap; remembers the level that worked on each
  band; stops if you change the power yourself; puts the power back when
  the station stops. Every change is listed with why. IC-9700 and IC-705;
  not while the minimum-power test step is on.
- **Satellites**: passes of the ISS (and any satellite you add) for your
  locator, worked out on this computer; orbital elements from CelesTrak
  when you press Update; live azimuth, elevation and distance; one button
  sets the station up for the ISS's APRS digipeater (145.825 MHz, path
  ARISS) and another puts your channel back. **Transmit only during a
  pass**: arming needs the satellite above the elevation you set (5° by
  default), and the station is disarmed when it goes below. **Doppler** on
  an IC-9700: receive and transmit corrected in small steps during the
  pass (transmit never mid-over), the radio put back after it.
- **23 cm DD (experimental)**: two IC-9700s in DD mode as a 128 kbit/s
  link between two computers. The app puts the radio in DD on a UK
  high-speed data channel and back afterwards, and carries all of packet
  over it (chat, link test, files, mail). You press TRANSMIT on the radio;
  the app cannot. See docs/DD.md for the network set-up - the radio sends
  to air whatever reaches its network port.

### New: a radio the controller's BBS holds

- **Settings › Controller shows a radio the OpenHamShack controller has
  reserved** (for its BBS and always-on conference room) as "reserved by"
  it, and this computer never opens it. The controller's BBS itself is an
  app on the controller, installed there, not part of this download.

### Changed

- AX.25 no longer under-estimates the round trip when acknowledgements
  come back slowly (a return path over the internet): T1 stays above it.
- A mail receipt (delivered, or could not be delivered) plays the chime
  alone, never "You've got mail!", which is for mail that came in.

## 0.2.3 (19/10/2026)

Everything in 0.2.2 is here, plus notifications, and fixes from Brian
and Ray (GM0OTB)'s on-air test on 19/10. Section numbers are the tester
guide's.

### New

- **Notifications** (section 3, *Notifications*). An APRS message to
  you, a line from a station in Connect / Chat, or a message left in your
  mailbox now pops up a desktop notification (who from, and the start of
  it) with a sound, when the window is not in front or is showing another
  page. The list on the left shows the page in bold with an unread count
  until you open it. Click the notification to go straight there. Your
  own messages heard back, repeats of a message already shown, and
  messages to other stations never notify.
- **Settings › Notifications**: on or off, a switch for each kind, and
  the sound: **Beep** (the default), **Spoken: 'You've got mail!'
  (retro)** - a short chime made by the app and your computer's own voice
  saying "You've got mail!", "You've got a message!" or "New chat!" - or
  a WAV file of your own. Where your computer cannot speak or play the
  file, it beeps. Nothing recorded is bundled.
- **Transmit gain in Settings › Channel** (Pluto): the same list as the
  setup check's, under the band's amplifier drive cap, so it can be
  changed without going through the setup check.
- **Find beside the Pluto's address** in the setup check: it looks for
  Plutos on USB and the network (the way QO-100 NB finds them: each
  libiio backend, then quick looks at the usual addresses) and fills in
  the one you pick. It only reads; nothing on the Pluto is changed.

### Fixed

- **Link test: the RF receiver leaving no longer throws the sender
  out.** The link is dropped (nothing more is sent), but the sender stays
  joined, says the other station left, and carries on when it joins
  again.
- **Link test speed**: a second run on the same link showed twice the
  real rate (1921 bit/s at 1200 baud). Each run now counts its own bytes.
- **Link test: 1200 baud AFSK "occupies 0 kHz"** - it occupies 9 kHz
  (measured through the Pluto), inside a 12.5 kHz channel.
- **IC-9700/IC-705 on the network with a wrong password** was tried
  again every second or so for ever. It now stops at the first refusal,
  says so in the status bar and the setup check, and tries again only
  when you press Reconnect or change the settings. Other connection
  failures wait longer each time (2, 4, 8 ... up to 60 s).
- **Pluto: busy stayed lit for a minute on an empty channel** and held
  transmit off. The cause found: a mode the link had stopped listening to
  kept its "busy" from a frame cut off half way. Busy now comes only from
  the modes being listened to. And whatever the cause, busy is now waited
  on for 15 s at most (longer than any packet station's over): after that
  the station goes ahead as if the channel were clear, the usual random
  delay still applying, and says so in its log.
- **Link test: each received frame's C/N0 and SNR showed "-"** on a
  Pluto in one mode. They are now filled in, 1200 baud included.
## 0.2.2 (19/10/2026)

Everything in 0.2.1 is here, plus fixes to the setup check's *Your
radio* page for a Pluto, from Brian's first run on a Mac. Section
numbers are the tester guide's.

### Changed

- **Transmit gain is a list**, 0 dB down to -60 dB in 2 dB steps,
  instead of a box with tiny arrows (section 5). **A new Pluto starts at
  -30 dB** (it was -10 dB, close to full power). A gain saved by 0.2.1
  opens on the step at or below it (-9.5 becomes -10). With an
  amplifier's drive cap set for the band (Fast modes and amplifier),
  gains above the cap are greyed out and a saved gain above it comes
  down to it.
- **On the OpenHamShack controller** now offers to pair with your
  controller there and then (the same page as Settings › Controller,
  already looking for controllers), and fills in the controller Pluto's
  address. If none is paired, the address becomes
  `pluto://host?iiod=30431&ssh=30422` with `host` selected for you to
  type the controller's name or address.

### Fixed

- **The Pluto panel no longer squashes on a Mac**: the crystal
  correction's fields, its Save and Forget buttons and its notes drew
  over each other. The panel now scrolls instead.
- **Crystal correction no longer starts at 46.000000 MHz.** Both
  frequencies are blank until the Pluto is checked; then *It should be*
  is the frequency it was checked on, and *It was* waits for your
  reading.
- **Check this Pluto on the controller's Pluto** said "That is the one
  to use here", as if judged for this computer. It now says the mode is
  the one the computer the Pluto is plugged into uses. A controller's
  Pluto that is not on RNDIS is still offered the switch, because the
  controller runs Linux.

## 0.2.1 (19/10/2026)

Everything in 0.2.0 is here, plus fast packet on an ADALM-Pluto, two
test pages for trying a path with a friend, and better Pluto checks.
Section numbers are the tester guide's.

### New

- **Fast modes on a Pluto** (section 5, *Fast modes on a Pluto*).
  Settings › Fast modes: G3RUH 9600, 19 200 and 38 400 baud (heard by
  any G3RUH-compatible station), and, between two OpenHamShack stations
  only, GFSK 76 800 and 153 600 baud and 4-FSK 38 400 and 76 800 baud.
  Window up to 63, packet length up to 2048 bytes, selective reject.
  1200 baud AFSK stays the default: nothing changes until you choose a
  fast mode. The fast modes were on the air on 18/10 between two
  Plutos, 9600 to 153 600 baud.
- **Link adaptation**: both stations turn it on and each direction runs
  at the fastest mode its receiver can decode, stepping down when frames
  are lost and back up when the signal allows. Every acknowledgement goes
  at the slowest, most robust mode.
- **The band plan is checked for the wide modes.** Anything wider than
  25 kHz may go only in the high-speed data segments: on 23 cm
  1299.000-1299.750 MHz (and 1241.000-1241.750) in 150 kHz channels
  (1299.075, .225, .375, .525, .675), and GFSK 153 600 only on 13 cm
  2311-2315 MHz. 1298.500 is a 20 kHz channel: no wide mode there. The
  settings show where the Pluto's LO leakage and image land and offer
  1299.525 MHz; Arm is refused out of plan.
- **Path test** page (section 10): one station sends numbered frames in
  each mode, the other counts every mode at once - heard, missing,
  level, SNR and bad checksums - and says which mode the path carries.
  Save or copy the report; it also goes into Help › Save diagnostics.
  "Use for adaptation" starts link adaptation from that mode.
- **Link test with a friend** page (section 11): a connected-mode
  transfer when each station has one antenna and no transmit/receive
  switch. The RF receiver's answers go back through a relay on an
  OpenHamShack controller over the internet; a banner says so the whole
  time, and losing the relay drops the link at once without transmitting.
- **An amplifier after a Pluto**: a drive cap per band, a guided
  *Find the drive level…* helper that steps the transmit gain up in
  short bursts while you watch a power meter, and a PTT output for the
  amplifier's relay (the Pluto's GPO pins on ADI firmware, the firmware's
  own on PlutoDVB2 and Tezuka). **Put the band-pass filter after the
  amplifier, and power the amplifier only for transmit runs**: a Pluto
  that restarts transmits on 2450 MHz until it is quieted.
- **A Pluto on the OpenHamShack controller** (section 5): use a Pluto
  plugged into the controller over the network
  (`pluto://host?iiod=30431&ssh=30422`), filled in from the paired
  controller; refused while the controller has it reserved or in use.
  It needs a good wired network path (about 10 MB/s each way).
- **Check this Pluto** now shows the Pluto's USB network mode and the
  one to use on this computer - **ECM on a Mac, RNDIS on Linux and
  Windows** (NCM stalls receive on Linux and the Pluto's watchdog
  restarts it) - and offers to switch it, under the same precautions as
  the AD9364 change (it restarts the Pluto). On Windows, a Pluto with no
  network gets a link to ADI's PlutoSDR-M2k USB driver.

### Fixed

- Settings › Fast modes replace the link test's own Pluto mode list, so
  a link test runs the adaptive window (63 frames) instead of 4.
- Less CPU when receiving: the numerical library no longer keeps idle
  threads spinning (it tripled the fast receivers' CPU on a busy PC).

## 0.2.0 (18/10/2026)

Everything in 0.1.2 is here, plus the following. Fixes found on the air
after this release will come as 0.2.1, 0.2.2 and so on.
Section numbers are the tester guide's (docs/TESTER-GUIDE.md).

### New

- **The app sets your radio up for packet, and puts it back** (section 2).
  Opening an IC-9700, IC-705 or FTX-1 now switches off everything that
  spoils 1200 baud packet - noise reduction (NR), noise blanker, auto
  notch, the compressor, VOX, monitor and AFC - selects the wide FM data
  filter, routes the data audio from this computer, and reads every
  setting first so it can put it back when the app closes the radio. If
  the app or the computer crashes with the radio open, the next start puts
  the radio back before anything else. **Station › What the app changed on
  the radio…** lists every setting before and after, what it could not
  set, and what you must set by hand.
- **NR off for packet.** On 18/10 an IC-9700's noise reduction stopped 2 m
  packet decoding completely: it reshapes the two tones during each
  packet. The set-up above switches it off; if you ever use the radio for
  packet with another program, switch NR off yourself (section 5).
- **Warnings that matter before you transmit**, in the status bar and the
  set-up report: an **IC-9700 transmitting packet over USB** (its USB
  sound device drops and repeats samples while transmitting, enough to
  stop other stations decoding on some radios - **use the LAN connection
  to transmit**), and a radio's USB sound device sharing a USB hub with an
  SDR.
- **An SDR as your radio** (section 5): an **ADALM-Pluto** (transmit and
  receive), a **HackRF One** (receive, and half-duplex transmit that is
  **experimental: not yet tested on the air**), or a
  **receive-only SDR** (RTL-SDR, an rtl_tcp server, or an SDRplay RSP).
  The Pluto runs a transmitter guard on the Pluto itself, so a crashed or
  frozen computer cannot leave it keyed; no guard, no transmit. The setup
  check can check a Pluto (model, firmware, frequency range), enable
  70 MHz-6 GHz on ADI firmware (AD9364; it transmits as the Pluto restarts,
  so it asks for a dummy load first), and measure and keep each Pluto's
  crystal correction. **An SDR transmits strong harmonics: transmit is
  refused until you confirm a band-pass filter is fitted.**
- **The APRS map** has coastlines, borders and towns built in and needs no
  internet. Stations show their APRS symbol, moving stations leave a
  track, and lines show whether a station was heard direct or through a
  digipeater. Click a station for its details. OpenStreetMap can be shown
  under it (Settings › Map), off unless you turn it on.
- **SDR monitor channels**: choose 2 m or 70 cm and the channels to watch
  (Settings › SDR monitor). The UK 70 cm packet channels now follow the
  RSGB band plan.
- **AKP03E control pad** support: arm (two presses), disarm, halt, beacon,
  connect, disconnect and page keys. No key transmits by itself.
- **The program updates itself** (section 1) from openhamshack.com, for
  copies installed with the .deb, the Windows installer or the Mac disk
  image: a check at start-up and once a day, a banner when an update is
  ready, and **Help › Check for updates…**. Nothing installs while you are
  transmitting or connected to a station. Updates are signed and checked
  before they are installed.
- **Help › Send diagnostics…** and an opt-in automatic upload (Settings ›
  Internet), off unless you turn it on (section 6). You get a code
  (SHACK-XXXX) to quote in an email.
- **OpenHamShack controllers**: Settings › Controller finds a controller on
  your network and pairs with it; the app then never opens a radio the
  controller is using.
- **Windows: the installer sets up Windows Defender Firewall** (section 1),
  so the radio's network audio is not silently blocked.

- **A HackRF One works on Windows and macOS too**: the installers now
  carry libhackrf (on Linux, install libhackrf0). Receive works as with
  any SDR; **HackRF transmit is experimental and has not been tested on
  the air yet**, and the radio choice says so.
- **The source code** of this release, and of every GPL and LGPL part
  the installers carry, is at
  https://openhamshack.com/source/#openhamshack-packet;
  THIRD-PARTY.txt in the program's licences folder lists what is inside.

### Fixed since 0.1.2

- **Chat no longer sends an empty line.** Pressing Enter on an empty chat
  line sent a packet holding nothing: a whole transmission for nothing.
- **A Pluto, or any radio that hears itself, can now connect to a
  station.** The app took its own connect request, heard back, for an
  incoming one and the link never started.
- **Long transmissions from a Pluto** should no longer run short of
  samples part way through (on 18/10 another station decoded little of a
  Pluto's 15-second transmissions). Fixed in the lab, not yet proved on
  the air: tell us if a long Pluto transmission is not decoded.
- **Over USB, the radio is told to stop transmitting before anything else
  queued**, and nothing waiting is lost when the radio is closed.
- An Icom radio that briefly refuses a setting is asked again before the
  app reports it.
- The UK 70 cm preset 432.500 MHz was wrong (it is the narrow-band SSTV
  centre, not APRS); 70 cm APRS is 433.800 MHz.

- **An IC-9700's duplex is only changed when it can be put back.** The
  set-up reads it first: a radio found on a repeater shift (DUP- or DUP+)
  is set to simplex and put back on close; with split on, it is left alone
  and the report asks you to check the radio is on simplex.

### Still the same

- The APRS identifier is still the experimental **APZOHS**; a registered
  one is applied for and will replace it.
- Everything is 1200 baud.

## 0.1.2, 18/10/2026 (Linux, Windows and macOS)

- **Keying no longer stalls while the program is listening.** With an
  IC-9700 over the network, transmissions could fail to start with a
  timeout, and the radio could go up to 15 seconds without answering the
  program, whenever received audio was arriving. Received audio is now
  decoded separately from the radio's connection, so a transmit command
  reaches the radio at once however busy the decoder is. This applies to
  every radio the program supports.
- If the computer cannot decode audio as fast as it arrives, the oldest
  audio is skipped rather than held up, and the log says so.
- The program's pages no longer pause while a transmission starts.

## 0.1.1 (Windows only), 18/10/2026

A fix release for the outside test, after Ray (GM0OTB) ran 0.1.0 with an
IC-9700 on USB.

- **Help › Save diagnostics no longer closes the program.** In 0.1.0 it
  closed at once on every system, with no file dialog and no zip. It now asks
  where to save, as it should, and if the save fails for any reason it says
  why instead of closing.
- **A diagnostics zip now carries the log from before a restart** (the end of
  the program's log file) and the record of any hard crash, so a bundle saved
  after the program closed unexpectedly still shows what happened.
- **More ways to get the radio off the air.** Ray's IC-9700 stayed on
  transmit for about two minutes. The cause is not known for certain yet, so
  0.1.1 adds several defences:
  - After every transmission the program asks the radio whether it has
    stopped, and tells it again if not. If the radio still will not confirm,
    the program tells you to check it and switch it to receive by hand.
  - A second watchdog, independent of the rest of the program, unkeys the
    radio if it has been keyed for a second past the transmit limit (15 s).
  - If the sound device stops responding when a transmission starts, the
    transmission is abandoned instead of waiting forever.
  - The radio is unkeyed if the program crashes or quits by any route.
  - A slow radio connection can no longer let a "transmit" command arrive
    after the "stop" that should have cancelled it.
- An IC-9700 or IC-705 set to "network" with no address is reported once
  in the station bar instead of being retried in the background forever.
- A turnaround measurement that sees no carrier on the radio's power meter
  says so instead of failing with an error.

## 0.1.0, 18/10/2026

First release to testers: Linux (.deb, AppImage), Windows and macOS.
