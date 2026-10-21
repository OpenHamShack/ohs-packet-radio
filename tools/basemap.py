#!/usr/bin/env python3
"""Build the APRS map's built-in basemap: coastlines, land borders and towns,
into one compressed file the app carries, so the map works with no network.

    tools/basemap.py --sources DIR [--date YYYY-MM-DD] [--fetch]

DIR holds the downloads, fetched once by hand (or with --fetch, which
downloads exactly the five files below and nothing else; never a crawler):

| file | from | licence |
|---|---|---|
| ne_10m_coastline.geojson, ne_10m_admin_0_boundary_lines_land.geojson, ne_50m_coastline.geojson, ne_50m_admin_0_boundary_lines_land.geojson | Natural Earth, https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ | public domain ("No permission is needed to use Natural Earth", naturalearthdata.com/about/terms-of-use) |
| cities5000.zip | GeoNames, https://download.geonames.org/export/dump/cities5000.zip | CC BY 4.0, credit "GeoNames" with a link (download.geonames.org/export/dump/readme.txt) |

It writes src/shack_packet_app/data/basemap-<date>.npz (the date the
sources were fetched, in the name, as the family keeps fetched data: GUIDE
§5, Ku Sweep's transponder list) and data/basemap.json, which names that
file and records each source's URL, licence, date and sha256. The app reads
basemap.json to find the file (basemap.py), so a refresh is a new dated
file, the manifest rewritten, and the old file deleted, in one commit.

What is kept, and why that much:
- **Coastlines and land borders at two scales.** 1:10m for a packet map's
  usual view (tens to a few hundred kilometres across), 1:50m for a view of
  a continent, where 1:10m would be millions of points nobody can see.
- **Thinned at build time** (Douglas-Peucker, `thin`): to within 0.001
  degree (about 100 m) for 1:10m and 0.01 for 1:50m. Natural Earth's own
  1:10m accuracy is no better than that, and the map never shows less than
  four kilometres across.
- **Cut into runs of at most 128 points**, each with its box, so the map
  draws only the runs it can see.
- **Towns of 5000 people or more** (GeoNames cities5000): name, place and
  population, largest first, so the map can label the biggest it has room
  for. Natural Earth's own populated places are public domain but leave
  out most small UK towns, which is where packet stations are (PLAN-0.2 D6).

Coordinates are stored as integers of 1e-4 degree (about 11 m, far finer
than the data), each run's first point as it is (`first`, int32) and the
rest as differences from the point before (`d`, int16), which numpy's zip
compression packs well: 1e-5 degree and int32 throughout made the file
twice the size for nothing anyone could see.

Stdlib and numpy only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import sys
import urllib.request
import zipfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "src" / "shack_packet_app" / "data"
NE_URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/"
GEONAMES_URL = "https://download.geonames.org/export/dump/cities5000.zip"
# layer: (Natural Earth file, thinning tolerance in degrees)
NE_FILES = {"coast10": ("ne_10m_coastline.geojson", 0.001),
            "border10": ("ne_10m_admin_0_boundary_lines_land.geojson", 0.001),
            "coast50": ("ne_50m_coastline.geojson", 0.01),
            "border50": ("ne_50m_admin_0_boundary_lines_land.geojson", 0.01)}
RUN = 128
SCALE = 1e4
NE_LICENCE = ("Public domain. \"All versions of Natural Earth raster + vector map data found on this website are "
              "in the public domain.\" (naturalearthdata.com/about/terms-of-use)")
GEONAMES_LICENCE = "CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/); credit GeoNames, geonames.org"
USER_AGENT = "OpenHamShack-Packet-Radio-basemap/1 (+https://openhamshack.com)"


def _lines(geojson: dict):
    for f in geojson["features"]:
        g = f["geometry"]
        if g is None:
            continue
        if g["type"] == "LineString":
            yield g["coordinates"]
        elif g["type"] == "MultiLineString":
            yield from g["coordinates"]


def thin(points: np.ndarray, tol: float) -> np.ndarray:
    """Douglas-Peucker: keep the fewest points that stay within `tol`
    degrees of the line (in plain degrees, which is close enough at this
    tolerance). Its ends are always kept, so a line still meets the next."""
    n = len(points)
    if n <= 2:
        return points
    keep = np.zeros(n, dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        a, b = points[i], points[j]
        seg = points[i + 1:j]
        ab = b - a
        length = float(np.hypot(*ab))
        if length == 0.0:
            dist = np.hypot(*(seg - a).T)
        else:
            dist = np.abs(ab[0] * (seg[:, 1] - a[1]) - ab[1] * (seg[:, 0] - a[0])) / length
        k = int(np.argmax(dist))
        if dist[k] > tol:
            m = i + 1 + k
            keep[m] = True
            stack += [(i, m), (m, j)]
    return points[keep]


def densify(points: np.ndarray, most: float = 3.0) -> np.ndarray:
    """Points added along any step longer than `most` degrees, so every
    step fits the int16 differences (a few borders run straight for many
    degrees, the 141st meridian between the Papua provinces for one)."""
    out = [points[:1]]
    for a, b in zip(points[:-1], points[1:]):
        n = int(np.ceil(np.abs(b - a).max() / most))
        if n > 1:
            out.append(a + (b - a) * np.arange(1, n)[:, None] / n)
        out.append(b[None])
    return np.concatenate(out)


def layer(geojson: dict, tol: float) -> dict[str, np.ndarray]:
    """{"d": int16 (n, 2) lon/lat differences from the point before (0 at
    a run's start); "first": int32 (runs, 2) each run's first point;
    "start": int32 run offsets; "box": float32 (runs, 4) min lon, min lat,
    max lon, max lat}."""
    runs = []
    for line in _lines(geojson):
        pts = densify(thin(np.asarray(line, dtype=np.float64)[:, :2], tol))
        for a in range(0, len(pts) - 1, RUN - 1):
            run = pts[a:a + RUN]
            if len(run) >= 2:
                runs.append(run)
    starts, boxes, parts = [], [], []
    n = 0
    for run in runs:
        q = np.round(run * SCALE).astype(np.int64)
        d = np.diff(q, axis=0, prepend=np.zeros((1, 2), dtype=np.int64))
        parts.append(d)
        starts.append(n)
        n += len(run)
        boxes.append([run[:, 0].min(), run[:, 1].min(), run[:, 0].max(), run[:, 1].max()])
    d = np.concatenate(parts)
    # A run's first point (absolute, up to 1.8 million) needs 32 bits; the
    # differences after it fit 16, and the starts are kept apart so the
    # bulk packs as int16.
    first = d[starts].astype(np.int32)
    d[starts] = 0
    assert np.abs(d).max() < 32768, "a step too long for int16: lower RUN or check the source"
    return {"d": d.astype(np.int16), "first": first, "start": np.asarray(starts, dtype=np.int32),
            "box": np.asarray(boxes, dtype=np.float32)}


def towns(zip_bytes: bytes) -> dict[str, np.ndarray]:
    """GeoNames cities5000: name (UTF-8, joined, with offsets), lat/lon in
    1e-4 degree, population; largest first."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        text = z.read("cities5000.txt").decode("utf-8")
    rows = []
    for line in text.splitlines():
        f = line.split("\t")
        if len(f) < 15:
            continue
        rows.append((int(f[14] or 0), f[1], float(f[4]), float(f[5])))
    rows.sort(key=lambda r: (-r[0], r[1]))
    names = [r[1].encode("utf-8") for r in rows]
    ends = np.cumsum([len(b) for b in names]).astype(np.int32)
    return {"names": np.frombuffer(b"".join(names), dtype=np.uint8), "name_end": ends,
            "lat": np.round(np.array([r[2] for r in rows]) * SCALE).astype(np.int32),
            "lon": np.round(np.array([r[3] for r in rows]) * SCALE).astype(np.int32),
            "pop": np.array([r[0] for r in rows], dtype=np.uint32)}


def fetch(dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for name in [f for f, _t in NE_FILES.values()]:
        _get(NE_URL + name, dest / name)
    _get(GEONAMES_URL, dest / "cities5000.zip")


def _get(url: str, path: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as r:
        path.write_bytes(r.read())
    print(f"fetched {url} -> {path}")


def build(sources: Path, date: str, out_dir: Path = DATA) -> Path:
    arrays, manifest_sources = {}, []
    for key, (name, tol) in NE_FILES.items():
        raw = (sources / name).read_bytes()
        for k, v in layer(json.loads(raw), tol).items():
            arrays[f"{key}_{k}"] = v
        manifest_sources.append({"layer": key, "file": name, "url": NE_URL + name, "licence": NE_LICENCE,
                                 "fetched": date, "sha256": hashlib.sha256(raw).hexdigest(), "thinnedDeg": tol})
    raw = (sources / "cities5000.zip").read_bytes()
    for k, v in towns(raw).items():
        arrays[f"towns_{k}"] = v
    manifest_sources.append({"layer": "towns", "file": "cities5000.zip", "url": GEONAMES_URL,
                             "licence": GEONAMES_LICENCE, "fetched": date, "sha256": hashlib.sha256(raw).hexdigest()})
    out = out_dir / f"basemap-{date}.npz"
    for old in out_dir.glob("basemap-*.npz"):
        if old != out:
            old.unlink()
    np.savez_compressed(out, **arrays)
    manifest = {"file": out.name, "scale": SCALE, "run": RUN,
                "attribution": "Natural Earth; towns: GeoNames (CC BY 4.0)",
                "comment": "Written by tools/basemap.py; do not edit by hand. The app finds the basemap through this "
                           "file (basemap.py).",
                "sources": manifest_sources}
    (out_dir / "basemap.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sources", type=Path, required=True)
    ap.add_argument("--date", default=dt.date.today().isoformat(), help="when the sources were fetched")
    ap.add_argument("--fetch", action="store_true", help="download the five source files into --sources first")
    args = ap.parse_args(argv)
    if args.fetch:
        fetch(args.sources)
    out = build(args.sources, args.date)
    print(f"{out}: {out.stat().st_size / 1e6:.2f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
