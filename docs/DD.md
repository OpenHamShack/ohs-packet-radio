# 23 cm DD transport (experimental, 0.3.0)

Packet Radio can use two IC-9700s in **DD mode** as a 128 kbit/s link
between two stations' computers on 23 cm. Off unless chosen (IC-9700 page ›
23 cm DD › Use DD). Nothing about it has been on the air yet: the first run
is the supervised chatter test below, with Brian present.

## What DD is (Icom Advanced Manual, section 13)

- In DD the radio is an **Ethernet bridge**: every frame that reaches its
  [LAN] port goes to air, and what it receives comes out of that port.
  The two PCs talk plain IP, on one private subnet, as if on one cable.
- The transmitter is gated by **TX INHIBIT**, toggled only by the
  front-panel **TRANSMIT** key. CI-V `1A 05 0087` is only its power-on
  default; the app cannot set the live one, so it asks the operator to
  press TRANSMIT and to press it again before stopping.
- `1A 05 0088` DD Packet Output (Normal / All) is read and reported, never
  written. RPS (`0F 13`) must be off for simplex; the app sets `0F 10`.
- UK placement: the 150 kHz high-speed data channels 1299.075, .225, .375,
  .525, .675 MHz (RSGB 2026). The link refuses any other frequency.

## How the app carries packet over it

**AX.25 over AXUDP** (shack_packet `radios/dd.py`): one frame per UDP
datagram with its FCS, port 10093 - the encapsulation BPQ32, LinBPQ and
JNOS use. The DD link is a frame-carrying link like a KISS TNC, so
everything the app does over AX.25 - chat, the link test, file transfer,
mail, IP - works over DD with no second protocol stack.

**No listening sockets.** The socket is bound to the PC's DD-side address
only (never 0.0.0.0) and `connect()`ed to the other station, so the kernel
hands it nothing from any other address and it answers nobody else; both
stations do the same, so each one's first datagram opens the flow, as a
hole punch does. Rejected: TCP with one side listening (a server in a
desktop app), and rendezvous through openhamshack.com's relay with the data
peer-to-peer (an internet dependency for a link that needs none).

**Transmit** is still gated by arming: every datagram is a transmission, so
the link refuses to send while the station is disarmed, exactly as an audio
link refuses to key. What the app cannot gate is *other* traffic from the
PC reaching the radio - hence the network set-ups below.

**The radio's set-up** (shack_rig `icom/satdd.dd_settings`, through the
same journalled read-save-set-restore as the packet set-up): the channel,
DD, RPS off, RF power if chosen. Put back on Stop DD, on close, and after a
crash at the next open. CI-V goes over the LAN as for packet (or USB);
whether LAN remote control keeps working in DD is one of the two things the
chatter test answers.

## Two network set-ups

**(a) Radio left on the home LAN** - only if the chatter test shows a 9700
in DD does *not* transmit ordinary LAN chatter (broadcasts, ARP, mDNS).
Then both PCs need a second address on the DD subnet (e.g. 10.99.0.1/24
and 10.99.0.2/24) on the interface facing the radio.

**(b) The controller's "Radio port"** - a second NIC on the OpenHamShack
controller box with only the radio on it, filtered so the one thing that
reaches the radio is the AXUDP flow. `tools/dd-radio-port.sh` does it:

```
sudo tools/dd-radio-port.sh apply --if enp2s0 --dd 10.99.0.1/24 --peer 10.99.0.2 \
                                  --lan-if enp1s0 --desktop 198.51.100.20 [--peer-mac aa:bb:..]
sudo tools/dd-radio-port.sh remove --if enp2s0
     tools/dd-radio-port.sh print ...      # what apply would do; changes nothing
```

It gives the port a static address with IPv6 off (no DHCP, SLAAC, router
solicitations or MLD), allows ARP only for the peer (or none, with a
permanent neighbour entry, given `--peer-mac`), and loads `table inet
ohs_dd`: out of the port only UDP 10093 to the peer, in only UDP 10093
from it, nothing else forwarded. The desktop sends to the box's LAN
address, port 10093, and the box rewrites the flow to the peer and the
replies back (DNAT + masquerade), so the desktop keeps a connected socket
and no listener: its DD settings are local `<desktop LAN IP>:10093`, peer
`<box LAN IP>:10093`. Nothing persists across a reboot. It is a script for
now; the controller platform wants it as a named privileged operation.

## The supervised chatter test

`tools/dd-chatter-test.py` (Brian present, at the radio, antenna or dummy
load of his choosing on 23 cm):

```
tools/dd-chatter-test.py --host 198.51.100.150 --user <user> --seconds 30 --out dd-result.json
tools/dd-chatter-test.py --dry-run --yes        # the same steps against FakeIcom, no radio
```

1. Saves the radio's state (journalled), sets 1299.525 MHz, DD, RPS off,
   RF POWER 0 (the 9700's minimum, about 0.5 W - not zero).
2. Asks Brian to press TRANSMIT (TX INHIBIT off). The script never keys.
3. Polls `1C 00` and `15 11` five times a second for `--seconds`, sending
   40 UDP broadcasts (port 9, discard) on the home LAN from a third of the
   way in.
4. Asks Brian to press TRANSMIT again, restores everything, and prints:
   how many polls CI-V over the LAN answered in DD (does remote control
   survive DD?), and whether the radio transmitted during the chatter
   (set-up a or b?).

Ctrl-C or any failure still restores; a killed run is recovered from the
journal by the next run before it changes anything.

## Untested

Everything on the air: DD itself, AXUDP across a real pair of 9700s, the
chatter question, LAN control in DD, the Radio port filter on a real box,
throughput (the channel reckons airtime at 76 800 baud to leave room for
Ethernet/IP/UDP headers and DD framing), and whether DD Packet Output
"All" changes what the radio forwards.
