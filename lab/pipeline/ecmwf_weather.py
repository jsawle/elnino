"""ECMWF weather maps for the Hurricane Explorer lab.

For each named storm in the newest forecast run, cuts a box around ECMWF's main
forecast track out of the IFS 0.25 degree forecast (ECMWF Open Data, CC BY 4.0)
and saves every 3-hourly frame from 0 to 144 hours as two PNG frame sheets:

  wind.png  R = 10 m wind east-west (u), G = 10 m wind north-south (v), B = rain in the 3 hours to this time
  air.png   R = total column water vapour (moisture), G = mean sea-level pressure

8-bit encodings (decode in the app):
  u, v   : byte/255*140 - 70           m/s   (steps of 0.55 m/s)
  rain   : (byte/255)^2 * 256          mm in 3 h (square-root scale, fine steps for light rain)
  tcwv   : byte/255*90                 kg/m2 (steps of 0.35)
  msl    : 900 + byte/255*150          hPa   (steps of 0.59 hPa)
"""
from __future__ import annotations

import datetime as dt
import json
import math
import sys
from pathlib import Path

import eccodes as ec
import numpy as np
from PIL import Image

WX_VERSION = "1.0"          # bump when the weather maths or data handling changes
STEPS = list(range(0, 145, 3))
PARAMS = ["10u", "10v", "tp", "tcwv", "msl"]
D = 0.25                    # grid spacing (degrees)
MARGIN = 8.0                # degrees of map around the track
MAX_LAT_SPAN, MAX_LON_SPAN = 36.0, 48.0
ENC = {"u": [-70, 140], "v": [-70, 140], "rain": [256, "sqrt"], "tcwv": [0, 90], "msl": [900, 150]}
CREDIT = "Adapted from ECMWF IFS forecast data by ECMWF, licensed under CC BY 4.0"


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- region
def main_track(storm: dict):
    """ECMWF's main (unperturbed) forecast track, else the ensemble's first track."""
    src = storm["sources"]
    for t in src.get("ifs-hres", []):
        return t
    ens = src.get("ifs-ens", []) or src.get("aifs-ens", [])
    for t in ens:
        if t["role"] == "control":
            return t
    return ens[0] if ens else None


def region_for(storm: dict):
    """(north, south, west, east) box in degrees, snapped to the 0.25 grid.
    Longitudes are kept continuous, so a box can run past 180."""
    tr = main_track(storm)
    if not tr:
        return None
    pts = [(la, lo) for h, la, lo in zip(tr["t"], tr["lat"], tr["lon"]) if h <= STEPS[-1]]
    if not pts:
        return None
    lat0, lon0 = pts[0]
    lons = []
    for _, lo in pts:  # unwrap relative to the start
        while lo - lon0 > 180: lo -= 360
        while lo - lon0 < -180: lo += 360
        lons.append(lo)
    lats = [la for la, _ in pts]
    n, s = max(lats) + MARGIN, min(lats) - MARGIN
    w, e = min(lons) - MARGIN, max(lons) + MARGIN
    # too big: keep the box around the start of the track
    if n - s > MAX_LAT_SPAN:
        c = min(max(lat0, s + MAX_LAT_SPAN / 2), n - MAX_LAT_SPAN / 2)
        n, s = c + MAX_LAT_SPAN / 2, c - MAX_LAT_SPAN / 2
    if e - w > MAX_LON_SPAN:
        c = min(max(lon0, w + MAX_LON_SPAN / 2), e - MAX_LON_SPAN / 2)
        w, e = c - MAX_LON_SPAN / 2, c + MAX_LON_SPAN / 2
    n, s = min(n, 85.0), max(s, -85.0)
    snap = lambda x, f: f(x / D) * D
    n, s, w, e = snap(n, math.floor), snap(s, math.ceil), snap(w, math.ceil), snap(e, math.floor)
    if w < -180 and e < -180 or w >= 180:  # whole box off the edge: shift by 360
        shift = 360 if w < -180 else -360
        w, e = w + shift, e + shift
    return n, s, w, e


# ---------------------------------------------------------------- download
def fetch_grib(run: dt.datetime, target: Path) -> str:
    """Download only the 5 fields we need (byte ranges via the .index files)."""
    from ecmwf.opendata import Client
    last = None
    for source in ("ecmwf", "aws"):
        try:
            Client(source=source, maximum_retries=3, retry_after=10).retrieve(
                date=run.strftime("%Y%m%d"), time=run.hour, stream="oper", type="fc",
                step=STEPS, param=PARAMS, target=str(target))
            log(f"  weather GRIB from {source}: {target.stat().st_size // 2**20} MB")
            return source
        except Exception as e:
            last = e
            log(f"  weather download from {source} failed: {e}")
    raise RuntimeError(f"weather download failed: {last}")


# ---------------------------------------------------------------- decode
def read_fields(grib: Path, regions: dict):
    """Return {storm_id: {param: {step: 2-D array (north to south, west to east)}}}."""
    out = {sid: {p: {} for p in PARAMS} for sid in regions}
    idx_cache = {}
    with open(grib, "rb") as f:
        while True:
            h = ec.codes_grib_new_from_file(f)
            if h is None:
                break
            try:
                sn = ec.codes_get(h, "shortName")
                if sn not in PARAMS:
                    continue
                step = int(ec.codes_get(h, "endStep"))
                ni, nj = ec.codes_get(h, "Ni"), ec.codes_get(h, "Nj")
                la1 = ec.codes_get(h, "latitudeOfFirstGridPointInDegrees")
                lo1 = ec.codes_get(h, "longitudeOfFirstGridPointInDegrees")
                di = ec.codes_get(h, "iDirectionIncrementInDegrees")
                dj = ec.codes_get(h, "jDirectionIncrementInDegrees")
                jpos = ec.codes_get(h, "jScansPositively")
                vals = ec.codes_get_values(h).reshape(nj, ni)
                for sid, (n, s, w, e) in regions.items():
                    key = (sid, ni, nj, la1, lo1, di, dj, jpos)
                    if key not in idx_cache:
                        lats = np.arange(n, s - D / 2, -D)
                        lons = np.arange(w, e + D / 2, D)
                        jj = np.rint(((lats - la1) if jpos else (la1 - lats)) / dj).astype(int)
                        ii = (np.rint(((lons - lo1) % 360) / di).astype(int)) % ni
                        idx_cache[key] = (np.clip(jj, 0, nj - 1), ii)
                    jj, ii = idx_cache[key]
                    out[sid][sn][step] = vals[np.ix_(jj, ii)].astype(np.float32)
            finally:
                ec.codes_release(h)
    return out


def enc(x, lo, span):
    return np.clip(np.rint((x - lo) / span * 255), 0, 255).astype(np.uint8)


def sheet(frames: list[np.ndarray], cols: int) -> np.ndarray:
    ny, nx = frames[0].shape[:2]
    rows = math.ceil(len(frames) / cols)
    img = np.zeros((rows * ny, cols * nx, 3), np.uint8)
    for k, fr in enumerate(frames):
        r, c = divmod(k, cols)
        img[r * ny:(r + 1) * ny, c * nx:(c + 1) * nx] = fr
    return img


def write_storm(sid: str, fields: dict, region, run: dt.datetime, out_dir: Path, rid: str):
    if 0 not in fields["tp"] and 0 in fields["msl"]:
        fields["tp"][0] = np.zeros_like(fields["msl"][0])  # rain so far is zero at the start
    steps = [s for s in STEPS if all(s in fields[p] for p in PARAMS)]
    if len(steps) < 2:
        raise RuntimeError(f"{sid}: only {len(steps)} complete time steps")
    n, s, w, e = region
    ny, nx = fields["msl"][steps[0]].shape
    wind, air = [], []
    for k, st in enumerate(steps):
        u, v = fields["10u"][st], fields["10v"][st]
        if k == 0:
            rain = np.zeros_like(u)
        else:
            rain = np.maximum(0, fields["tp"][st] - fields["tp"][steps[k - 1]]) * 1000.0  # m -> mm
            rain *= 3.0 / (st - steps[k - 1])  # per 3 h, if a step is missing
        rb = np.clip(np.rint(np.sqrt(np.clip(rain, 0, 256) / 256) * 255), 0, 255).astype(np.uint8)
        wind.append(np.dstack([enc(u, -70, 140), enc(v, -70, 140), rb]))
        air.append(np.dstack([enc(fields["tcwv"][st], 0, 90), enc(fields["msl"][st] / 100.0, 900, 150),
                              np.zeros((ny, nx), np.uint8)]))
    cols = math.ceil(math.sqrt(len(steps)))
    d = out_dir / "wx" / rid / sid
    d.mkdir(parents=True, exist_ok=True)
    Image.fromarray(sheet(wind, cols), "RGB").save(d / "wind.png", optimize=True)
    Image.fromarray(sheet(air, cols), "RGB").save(d / "air.png", optimize=True)
    meta = {"version": WX_VERSION, "run": run.strftime("%Y-%m-%dT%H:%MZ"), "storm": sid,
            "model": "ECMWF IFS (main forecast), 0.25 degree", "steps": steps,
            "nx": nx, "ny": ny, "north": n, "west": w, "d": D, "cols": cols,
            "files": {"wind": "wind.png", "air": "air.png"}, "enc": ENC, "credit": CREDIT}
    (d / "meta.json").write_text(json.dumps(meta, indent=1))
    kb = sum(p.stat().st_size for p in d.iterdir()) // 1024
    log(f"  weather {sid}: {nx}x{ny} cells, {len(steps)} frames, {kb} KB")
    return f"wx/{rid}/{sid}/meta.json", kb


def build(run: dt.datetime, rid: str, storms: list[dict], out_dir: Path, grib: Path | None = None,
          workdir: Path | None = None) -> dict:
    """Make weather maps for each named storm. Returns {storm_id: relative meta path}."""
    regions = {s["id"]: r for s in storms if (r := region_for(s))}
    if not regions:
        return {}
    if grib is None:
        grib = (workdir or out_dir) / f"wx-{rid}.grib2"
        fetch_grib(run, grib)
    fields = read_fields(grib, regions)
    done = {}
    for sid, region in regions.items():
        try:
            done[sid] = write_storm(sid, fields[sid], region, run, out_dir, rid)[0]
        except Exception as e:
            log(f"  weather {sid} skipped: {e}")
    return done
