"""tools/dd-radio-port.sh, in its print mode only (apply changes a
firewall and is never run by a test): the rule set it would load is valid
nftables - checked by nft itself inside a private network namespace, so
nothing on this machine changes - it passes only the AXUDP flow on the
radio port, and bad arguments are refused before anything is done."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parent.parent / "tools" / "dd-radio-port.sh"
ARGS = ["--if", "enp2s0", "--dd", "10.99.0.1/24", "--peer", "10.99.0.2", "--lan-if", "enp1s0",
        "--desktop", "198.51.100.20"]


def run(*args):
    return subprocess.run(["bash", str(TOOL), *args], capture_output=True, text=True, timeout=30)


def _nft():
    return shutil.which("nft") or next((p for p in ("/usr/sbin/nft", "/sbin/nft") if Path(p).exists()), None)


def test_the_rule_set_is_valid_nftables_and_drops_everything_else_on_the_port(tmp_path):
    r = run("print", *ARGS)
    assert r.returncode == 0, r.stderr
    rules = r.stdout[r.stdout.index("\ntable inet ohs_dd {") + 1:]
    lines = [ln.strip() for ln in rules.splitlines()]
    assert 'oifname "enp2s0" ip daddr 10.99.0.2 udp dport 10093 accept' in lines
    assert 'oifname "enp2s0" drop' in lines and 'iifname "enp2s0" drop' in lines
    assert "ip addr replace 10.99.0.1/24 dev enp2s0" in r.stdout and "disable_ipv6=1" in r.stdout
    assert 'oifname "enp2s0" arp daddr ip != 10.99.0.2 drop' in lines
    nft, unshare = _nft(), shutil.which("unshare")
    if nft is None or unshare is None:
        pytest.skip("no nft or unshare here to check the rule set with")
    f = tmp_path / "rules.nft"
    f.write_text(rules)
    check = subprocess.run([unshare, "-rn", nft, "-c", "-f", str(f)], capture_output=True, text=True, timeout=30)
    if check.returncode != 0 and "Operation not permitted" in check.stderr:
        pytest.skip("user namespaces are not allowed here")
    assert check.returncode == 0, check.stderr


def test_a_known_peer_mac_means_no_arp_at_all():
    r = run("print", *ARGS, "--peer-mac", "aa:bb:cc:dd:ee:ff")
    assert r.returncode == 0
    assert "nud permanent" in r.stdout and "arp off" in r.stdout and "table arp ohs_dd {" not in r.stdout


@pytest.mark.parametrize("bad", [["--dd", "10.99.0.1"], ["--peer", "10.99.0"], ["--if", "en p2s0"],
                                 ["--desktop", "pc.local"], ["--peer-mac", "aa:bb"], ["--port", "x"]])
def test_bad_arguments_are_refused_before_anything_happens(bad):
    args = list(ARGS)
    i = args.index(bad[0]) if bad[0] in args else None
    if i is None:
        args += bad
    else:
        args[i + 1] = bad[1]
    r = run("print", *args)
    assert r.returncode == 2 and "dd-radio-port:" in r.stderr and "table" not in r.stdout
