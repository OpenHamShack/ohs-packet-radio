#!/usr/bin/env bash
# The controller box's "Radio port" for 23 cm DD (set-up b in docs/DD.md):
# a second network port with the IC-9700's [LAN] port on it, filtered so
# the ONLY thing that reaches the radio - and so the only thing it sends to
# air - is Packet Radio's AXUDP between the two stations.
#
#   sudo tools/dd-radio-port.sh apply  --if enp2s0 --dd 10.99.0.1/24 --peer 10.99.0.2 \
#                                      --lan-if enp1s0 --desktop 198.51.100.20
#   sudo tools/dd-radio-port.sh remove --if enp2s0
#        tools/dd-radio-port.sh print  ...same options...    (what apply would do; changes nothing)
#
# Why each piece (a 9700 in DD bridges whatever Ethernet reaches it to air):
#  - Static address, no DHCP, IPv6 off on the port: DHCP, router
#    solicitations, MLD and SLAAC are all chatter that would be transmitted.
#  - ARP only for the peer (an arp-family table), or none at all with
#    --peer-mac (a permanent neighbour entry, ARP off on the port): an ARP
#    broadcast is a transmission too.
#  - nftables: out of the port, only UDP 10093 to the peer; in, only UDP
#    10093 from it; nothing forwarded to or from the port except that flow.
#  - The desktop app keeps its rule of no listening sockets: it sends to the
#    box's LAN address, port 10093, from its own port 10093, and the box
#    rewrites that flow to the peer (DNAT + masquerade) and the replies
#    back to the desktop, so the desktop's connected socket sees the peer as
#    the box. The DdLink there is local=<desktop>:10093, peer=<box LAN>:10093.
#  - Nothing is saved across a reboot: apply again after one. A rule set
#    left behind by accident is `remove`d by name (table inet ohs_dd).
#
# The OpenHamShack controller wants this as a named privileged operation
# (GUIDE: root only through validated verbs); until the broker grows one,
# this is a script Brian runs by hand, with sudo, on the box. Never on shack.
set -euo pipefail

action=${1:-}; shift || true
IF=""; DD=""; PEER=""; LAN_IF=""; DESKTOP=""; PEER_MAC=""; PORT=10093
while [ $# -gt 0 ]; do
    case "$1" in
        --if) IF=$2; shift 2 ;;
        --dd) DD=$2; shift 2 ;;
        --peer) PEER=$2; shift 2 ;;
        --lan-if) LAN_IF=$2; shift 2 ;;
        --desktop) DESKTOP=$2; shift 2 ;;
        --peer-mac) PEER_MAC=$2; shift 2 ;;
        --port) PORT=$2; shift 2 ;;
        *) echo "dd-radio-port: unknown option $1" >&2; exit 2 ;;
    esac
done

die() { echo "dd-radio-port: $*" >&2; exit 2; }
ipv4() { [[ $1 =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; }
ifname() { [[ $1 =~ ^[A-Za-z0-9_.-]{1,15}$ ]]; }

[ -n "$IF" ] && ifname "$IF" || die "--if: the radio port's interface name"
if [ "$action" = remove ]; then
    nft delete table inet ohs_dd 2>/dev/null || true
    nft delete table arp ohs_dd 2>/dev/null || true
    ip link set "$IF" arp on 2>/dev/null || true
    echo "dd-radio-port: filter removed from $IF (its address is left; ip addr flush dev $IF to clear it)"
    exit 0
fi
[ "$action" = apply ] || [ "$action" = print ] || die "usage: $0 apply|remove|print --if IF ..."
[[ $DD =~ ^([0-9.]+)/([0-9]{1,2})$ ]] && ipv4 "${BASH_REMATCH[1]}" || die "--dd: this box's DD address with its prefix, e.g. 10.99.0.1/24"
DD_ADDR=${DD%/*}
ipv4 "$PEER" || die "--peer: the other station's DD address"
[ -n "$LAN_IF" ] && ifname "$LAN_IF" || die "--lan-if: the box's home-LAN interface"
ipv4 "$DESKTOP" || die "--desktop: the desktop's home-LAN address"
[[ $PORT =~ ^[0-9]{1,5}$ ]] || die "--port: a UDP port"
if [ -n "$PEER_MAC" ]; then
    [[ $PEER_MAC =~ ^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$ ]] || die "--peer-mac: aa:bb:cc:dd:ee:ff"
fi

ruleset() {
cat <<EOF
table inet ohs_dd {
    chain prerouting {
        type nat hook prerouting priority dstnat;
        iifname "$LAN_IF" ip saddr $DESKTOP udp dport $PORT dnat ip to $PEER:$PORT
        iifname "$IF" ip saddr $PEER udp sport $PORT udp dport $PORT dnat ip to $DESKTOP:$PORT
    }
    chain postrouting {
        type nat hook postrouting priority srcnat;
        oifname "$IF" ip daddr $PEER udp dport $PORT snat ip to $DD_ADDR:$PORT
        oifname "$LAN_IF" ip daddr $DESKTOP udp sport $PORT masquerade
    }
    chain forward {
        type filter hook forward priority filter;
        iifname "$LAN_IF" oifname "$IF" ip saddr $DESKTOP ip daddr $PEER udp dport $PORT accept
        iifname "$IF" oifname "$LAN_IF" ip saddr $PEER ip daddr $DESKTOP udp sport $PORT accept
        iifname "$IF" drop
        oifname "$IF" drop
    }
    chain output {
        type filter hook output priority filter;
        oifname "$IF" ip daddr $PEER udp dport $PORT accept
        oifname "$IF" drop
    }
    chain input {
        type filter hook input priority filter;
        iifname "$IF" ip saddr $PEER udp dport $PORT accept
        iifname "$IF" drop
    }
}
EOF
if [ -z "$PEER_MAC" ]; then
cat <<EOF
table arp ohs_dd {
    chain output {
        type filter hook output priority filter;
        oifname "$IF" arp daddr ip != $PEER drop
    }
    chain input {
        type filter hook input priority filter;
        iifname "$IF" arp saddr ip != $PEER drop
    }
}
EOF
fi
}

commands() {
    echo "sysctl -qw net.ipv6.conf.$IF.disable_ipv6=1"
    echo "sysctl -qw net.ipv4.ip_forward=1"
    echo "ip addr replace $DD dev $IF"
    echo "ip link set $IF up"
    if [ -n "$PEER_MAC" ]; then
        echo "ip neigh replace $PEER lladdr $PEER_MAC dev $IF nud permanent"
        echo "ip link set $IF arp off"
    fi
    echo "nft delete table inet ohs_dd 2>/dev/null || true"
    echo "nft delete table arp ohs_dd 2>/dev/null || true"
    echo "nft -f <the rule set below>"
}

if [ "$action" = print ]; then
    commands
    echo
    ruleset
    exit 0
fi
[ "$(id -u)" = 0 ] || die "apply needs root (sudo)"
# The filter first, so the moment the port has an address nothing but the
# AXUDP flow can leave by it.
nft delete table inet ohs_dd 2>/dev/null || true
nft delete table arp ohs_dd 2>/dev/null || true
ruleset | nft -f -
sysctl -qw "net.ipv6.conf.$IF.disable_ipv6=1"
sysctl -qw net.ipv4.ip_forward=1
ip addr replace "$DD" dev "$IF"
ip link set "$IF" up
if [ -n "$PEER_MAC" ]; then
    ip neigh replace "$PEER" lladdr "$PEER_MAC" dev "$IF" nud permanent
    ip link set "$IF" arp off
fi
echo "dd-radio-port: $IF is $DD, passing only UDP $PORT between $DESKTOP (via $LAN_IF) and $PEER"
