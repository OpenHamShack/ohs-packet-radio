"""Channel presets and the band-plan question, from data, never constants.

Two sources, both data:

- the shared band-plan dataset (ohs-libs shack_bandplan), which knows the
  station's country, plan and licence: the APRS frequency comes from the
  plan's own centres (RSGB 144.800, IARU Region 2 144.390...), and every
  preset, and every arming, is judged by `shack_bandplan.check` for this
  station's licence class - a Foundation licensee and a Full licensee get
  different answers, and a station abroad gets its country's;
- `data/presets.json`, the packet channels the dataset does not carry yet
  (the UK node channels, the ISS), in the dataset's own presets shape, so
  that moving them into the dataset later changes this file and nothing
  else.

A preset the licence does not allow is still listed, disabled, with the
dataset's sentence saying why (BANDPLAN-DATA.md §4.6: never hidden).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources

import shack_bandplan as bp

#: A 1200-baud AFSK packet signal on FM: about 11 kHz occupied (Carson's
#: rule with 3 kHz deviation and a 2.2 kHz top tone is 10.4 kHz).
PACKET_BW_HZ = 11_000
PACKET_MODE = "digi"


@dataclass
class Preset:
    id: str
    label: str
    hz: int
    role: str
    enabled: bool = True
    why: str = ""


def _data() -> dict:
    text = resources.files(__package__).joinpath("data/presets.json").read_text(encoding="utf-8")
    return json.loads(text)


def context(profile: dict):
    return bp.context(profile)


def judge(profile: dict, hz: float) -> dict:
    """shack_bandplan's verdict for a packet signal on `hz` from this
    station: {"allowed", "refusal" (a sentence or ""), "warnings"}."""
    ctx = context(profile)
    v = bp.check(ctx, bp.Signal(centre_hz=float(hz), occupied_bw_hz=PACKET_BW_HZ, mode=PACKET_MODE))
    return {"allowed": bool(v["allowed"]), "refusal": (v["refusal"] or {}).get("text", ""),
            "warnings": [w.get("text", "") for w in v.get("warnings", [])], "verdict": v}


def _aprs_centre(ctx) -> int | None:
    for c in (ctx.plan or {}).get("centres", []):
        if "APRS" in str(c.get("label", "")).upper() and 144e6 <= c["hz"] <= 148e6:
            return int(c["hz"])
    return None


def presets(profile: dict) -> list[Preset]:
    ctx = context(profile)
    entries = [e for e in _data()["presets"] if e["kind"] == "packet"]
    pick = (next((e for e in entries if ctx.country_iso and e.get("country") == ctx.country_iso), None)
            or next((e for e in entries if ctx.region and e.get("region") == ctx.region), None)
            or next((e for e in entries if e.get("everywhere")), None))
    rows = [dict(p) for p in (pick or {}).get("presets", [])]
    aprs = _aprs_centre(ctx)
    if aprs is not None:
        for r in rows:
            if r["id"] == "aprs":
                r["txHz"] = aprs
    out = []
    for r in rows:
        v = bp.check(ctx, bp.Signal(centre_hz=float(r["txHz"]), occupied_bw_hz=PACKET_BW_HZ, mode=PACKET_MODE))
        out.append(Preset(r["id"], r["label"], int(r["txHz"]), r.get("role", "other"), bool(v["allowed"]),
                          (v["refusal"] or {}).get("text", "")))
    return out


def licence_classes(profile: dict) -> list[tuple[str, str]]:
    """(id, name) of the classes of the licence the dataset finds for this
    profile's country; [] when it has none."""
    ctx = context(profile)
    return [(c["id"], c["name"]) for c in (ctx.regime or {}).get("classes", [])]


def countries() -> list[tuple[str, str]]:
    """(ISO code, name) of every country the dataset knows."""
    data = bp.bundled()
    return sorted(((c["iso"], c["name"]) for c in data.countries), key=lambda x: x[1])
