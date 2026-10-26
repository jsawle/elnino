#!/usr/bin/env python3
"""ECMWF tropical cyclone track decoder for the Hurricane Explorer lab.

Downloads the tropical cyclone track files (BUFR) from ECMWF Open Data for the
newest 00 or 12 UTC run, decodes every storm and every forecast (ensemble
member), and writes compact JSON that the lab page reads.

Data: ECMWF Open Data, CC BY 4.0 (https://www.ecmwf.int/en/forecasts/datasets/open-data).
Track files are only published when ECMWF has a storm to track.

Usage:
  python ecmwf_tracks.py --out lab/data/ecmwf            # fetch newest run
  python ecmwf_tracks.py --out DIR --files a.bufr b.bufr  # decode local files (testing)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import sys
import tempfile
import urllib.request
from pathlib import Path

import eccodes as ec

DECODER_VERSION = "1.0"   # bump when decoding or data handling changes
KEEP_RUNS = 8             # archive size (00 and 12 UTC runs, about 4 days)
MS_TO_KT = 1.943844

# Where to look. ECMWF's own server first, then the AWS mirror.
BASES = {
    "ecmwf": "https://data.ecmwf.int/forecasts",
    "aws": "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com",
}
# Track products, in order of importance. "key" is the name used in the JSON.
PRODUCTS = [
    {"key": "ifs-ens", "model": "ifs", "stream": "enfo", "label": "ECMWF IFS ensemble"},
    {"key": "ifs-hres", "model": "ifs", "stream": "oper", "label": "ECMWF IFS HRES"},
    {"key": "aifs-ens", "model": "aifs-ens", "stream": "enfo", "label": "ECMWF AIFS ensemble"},
]
GUESS_STEPS = [360, 240, 144, 90]
UA = {"User-Agent": "hurricane-explorer-lab (github.com/jsawle/elnino)"}


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- download
def http_get(url: str, timeout=60) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception as e:  # 404 is normal: no storms, or run not out yet
        log(f"  miss {url} ({e})")
        return None


def run_dir(base: str, run: dt.datetime, model: str, stream: str) -> str:
    return f"{base}/{run:%Y%m%d}/{run:%H}z/{model}/0p25/{stream}/"


def find_tf_url(run: dt.datetime, model: str, stream: str) -> str | None:
    """Return the URL of the tf (track) BUFR file for this run, or None."""
    want = re.compile(rf"{run:%Y%m%d%H}0000-(\d+)h-{stream}-tf\.bufr")
    # 1. ECMWF directory listing (HTML)
    d = run_dir(BASES["ecmwf"], run, model, stream)
    html = http_get(d, timeout=30)
    if html:
        m = want.search(html.decode("utf-8", "replace"))
        if m:
            return d + m.group(0)
    # 2. AWS bucket listing (XML)
    prefix = f"{run:%Y%m%d}/{run:%H}z/{model}/0p25/{stream}/"
    xml = http_get(f"{BASES['aws']}/?list-type=2&prefix={prefix}", timeout=30)
    if xml:
        m = want.search(xml.decode("utf-8", "replace"))
        if m:
            return f"{BASES['aws']}/{prefix}{m.group(0)}"
    if html or (xml and b"<Key>" in xml):
        return None  # a listing worked and there is no track file (no storms, or not out yet)
    # 3. Neither listing worked: guess the file name
    for base in BASES.values():
        for step in GUESS_STEPS:
            url = run_dir(base, run, model, stream) + f"{run:%Y%m%d%H}0000-{step}h-{stream}-tf.bufr"
            try:
                req = urllib.request.Request(url, headers=UA, method="HEAD")
                with urllib.request.urlopen(req, timeout=20) as r:
                    if r.status == 200:
                        return url
            except Exception:
                pass
    return None


# ---------------------------------------------------------------- decode
def _ok(x) -> bool:
    return x is not None and not (isinstance(x, float) and math.isnan(x)) and abs(x) < 1e90


def _arr(b, key, n):
    """Array for key, expanded to n subsets; missing -> None."""
    try:
        a = ec.codes_get_array(b, key)
    except Exception:
        return [None] * n
    vals = [float(v) for v in a]
    if len(vals) == 1:
        vals = vals * n
    return [v if _ok(v) else None for v in vals[:n]] + [None] * max(0, n - len(vals))


def _first(b, key):
    try:
        a = ec.codes_get_array(b, key)
    except Exception:
        return None
    for v in a:
        v = float(v)
        if _ok(v) and v != 2147483647:
            return v
    return None


def _role(t):
    # ensembleForecastType: 0 deterministic (HRES), 1 control, others perturbed
    if t == 0:
        return "hres"
    if t == 1:
        return "control"
    return "member"


def decode_file(path: str, source: str) -> tuple[dt.datetime | None, list[dict]]:
    """Decode one ECMWF tc-track BUFR file into storms with per-forecast tracks."""
    storms, run = [], None
    with open(path, "rb") as f:
        while True:
            b = ec.codes_bufr_new_from_file(f)
            if b is None:
                break
            try:
                ec.codes_set(b, "unpack", 1)
                sid = ec.codes_get(b, "stormIdentifier").strip()
                name = ec.codes_get(b, "longStormName").strip()
                origin = dt.datetime(ec.codes_get(b, "year"), ec.codes_get(b, "month"),
                                     ec.codes_get(b, "day"), ec.codes_get(b, "hour"),
                                     ec.codes_get(b, "minute"), tzinfo=dt.timezone.utc)
                run = run or origin
                try:
                    n_steps = ec.codes_get_size(b, "timePeriod") + 1
                except Exception:
                    log(f"  {sid} {name}: no time steps, skipped")
                    continue
                n = ec.codes_get(b, "numberOfSubsets")
                mem = _arr(b, "ensembleMemberNumber", n)
                typ = _arr(b, "ensembleForecastType", n)
                lat = [_arr(b, "#2#latitude", n)]
                lon = [_arr(b, "#2#longitude", n)]
                pre = [_arr(b, "#1#pressureReducedToMeanSeaLevel", n)]
                wnd = [_arr(b, "#1#windSpeedAt10M", n)]
                hours = [0]
                for i in range(1, n_steps):
                    r1, r3 = i * 2 + 2, i * 2 + 3
                    if _first(b, f"#{r1}#meteorologicalAttributeSignificance") != 1 or \
                       _first(b, f"#{r3}#meteorologicalAttributeSignificance") != 3:
                        raise ValueError(f"unexpected layout at step {i}")
                    hours.append(int(_first(b, f"#{i}#timePeriod") or 0))
                    lat.append(_arr(b, f"#{r1}#latitude", n))
                    lon.append(_arr(b, f"#{r1}#longitude", n))
                    pre.append(_arr(b, f"#{i + 1}#pressureReducedToMeanSeaLevel", n))
                    wnd.append(_arr(b, f"#{i + 1}#windSpeedAt10M", n))
                tracks = []
                for k in range(n):
                    T, LA, LO, P, W = [], [], [], [], []
                    for s in range(n_steps):
                        la, lo = lat[s][k], lon[s][k]
                        if la is None or lo is None:
                            continue
                        lo = ((lo + 180) % 360) - 180
                        T.append(hours[s]); LA.append(round(la, 1)); LO.append(round(lo, 1))
                        P.append(round(pre[s][k] / 100) if pre[s][k] is not None else None)
                        W.append(round(wnd[s][k] * MS_TO_KT) if wnd[s][k] is not None else None)
                    if len(T) < 2:
                        continue
                    t = typ[k]
                    tracks.append({"m": int(mem[k]) if mem[k] is not None else k + 1,
                                   "role": _role(int(t) if t is not None else 4),
                                   "t": T, "lat": LA, "lon": LO, "p": P, "w": W})
                if tracks:
                    num = re.match(r"(\d+)", sid)
                    storms.append({"id": sid, "name": name, "basin": sid[-1:] if sid else "",
                                   "genesis": bool(num and int(num.group(1)) >= 70),
                                   "run": origin.strftime("%Y-%m-%dT%H:%MZ"),
                                   "source": source, "tracks": tracks})
            except Exception as e:
                log(f"  message skipped: {e}")
            finally:
                ec.codes_release(b)
    return run, storms


def add_product(run_storms: dict, key: str, storms: list[dict]) -> bool:
    """Store one product's storms. The deterministic (HRES) track can sit inside
    the IFS ensemble file as forecast type 0, so it is split out into ifs-hres."""
    if key == "ifs-hres":
        if "ifs-hres" in run_storms:
            return False  # already taken from the ensemble file
        for s in storms:
            for t in s["tracks"]:
                t["role"] = "hres"
    if key in ("ifs-ens", "aifs-ens"):
        hres = []
        for s in storms:
            h = [t for t in s["tracks"] if t["role"] == "hres"]
            s["tracks"] = [t for t in s["tracks"] if t["role"] != "hres"]
            if h and key == "ifs-ens":
                hres.append({**s, "tracks": h, "source": "ifs-hres"})
        if hres:
            run_storms["ifs-hres"] = hres
    run_storms[key] = storms
    return True


def merge(run_storms: dict[str, list[dict]]) -> list[dict]:
    """Combine storms from each product by storm ID into one list."""
    out: dict[str, dict] = {}
    for key, storms in run_storms.items():
        for s in storms:
            o = out.setdefault(s["id"], {"id": s["id"], "name": s["name"], "basin": s["basin"],
                                         "genesis": s["genesis"], "sources": {}})
            if o["name"] == o["id"] and s["name"] != s["id"]:
                o["name"] = s["name"]
            o["sources"][key] = s["tracks"]
    res = []
    for o in out.values():
        n_ens = len(o["sources"].get("ifs-ens", []))
        # Keep named storms, and developing systems that at least 10 forecasts show
        if o["genesis"] and n_ens < 10 and len(o["sources"].get("aifs-ens", [])) < 10:
            continue
        res.append(o)
    res.sort(key=lambda o: (o["genesis"], -len(o["sources"].get("ifs-ens", []))))
    return res


# ---------------------------------------------------------------- main
def candidate_runs(now: dt.datetime, days=3):
    t = now.replace(minute=0, second=0, microsecond=0)
    t -= dt.timedelta(hours=t.hour % 12)
    for i in range(days * 2):
        yield t - dt.timedelta(hours=12 * i)


def write_run(out: Path, run: dt.datetime, storms: list[dict], urls: dict, note: str = ""):
    rid = run.strftime("%Y%m%d%H")
    doc = {"run": run.strftime("%Y-%m-%dT%H:%MZ"), "decoder": DECODER_VERSION,
           "files": urls, "storms": storms,
           "credit": "Adapted from ECMWF tropical cyclone track forecasts by ECMWF, licensed under CC BY 4.0"}
    (out / "runs").mkdir(parents=True, exist_ok=True)
    p = out / "runs" / f"{rid}.json"
    p.write_text(json.dumps(doc, separators=(",", ":")))
    log(f"wrote {p} ({p.stat().st_size // 1024} KB, {len(storms)} storms) {note}")
    return rid


def update_index(out: Path, status: dict):
    idx_p = out / "index.json"
    runs = sorted((p.stem for p in (out / "runs").glob("*.json")), reverse=True) if (out / "runs").exists() else []
    for old in runs[KEEP_RUNS:]:
        (out / "runs" / f"{old}.json").unlink()
    runs = runs[:KEEP_RUNS]
    entries = []
    for rid in runs:
        d = json.loads((out / "runs" / f"{rid}.json").read_text())
        entries.append({"id": rid, "run": d["run"], "file": f"runs/{rid}.json",
                        "storms": [{"id": s["id"], "name": s["name"], "basin": s["basin"],
                                    "genesis": s["genesis"],
                                    "n": {k: len(v) for k, v in s["sources"].items()}} for s in d["storms"]]})
    idx = {"decoder": DECODER_VERSION, "updated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
           "latest": runs[0] if runs else None, "runs": entries, "status": status,
           "credit": "Adapted from ECMWF tropical cyclone track forecasts by ECMWF, licensed under CC BY 4.0",
           "licence": "https://creativecommons.org/licenses/by/4.0/"}
    # Only rewrite when something other than the timestamps changed (keeps commits rare)
    if idx_p.exists():
        try:
            old = json.loads(idx_p.read_text())
            strip = lambda d: {**d, "updated": None, "status": {**d.get("status", {}), "checked": None}}
            if strip(old) == strip(idx):
                log("index unchanged")
                return
        except Exception:
            pass
    idx_p.write_text(json.dumps(idx, indent=1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="lab/data/ecmwf")
    ap.add_argument("--files", nargs="*", help="decode local BUFR files instead of downloading")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc)
    status = {"checked": now.strftime("%Y-%m-%dT%H:%MZ"), "ok": False, "message": ""}

    if a.files:
        run_storms, run = {}, None
        for fpath in a.files:
            r, storms = decode_file(fpath, "ifs-ens")
            run = run or r
            add_product(run_storms, "ifs-ens", storms)
        if run:
            write_run(out, run, merge(run_storms), {"ifs-ens": [os.path.basename(x) for x in a.files]}, "(local files)")
            status.update(ok=True, message=f"Decoded {len(a.files)} local file(s)")
        update_index(out, status)
        return

    existing = {p.stem for p in (out / "runs").glob("*.json")} if (out / "runs").exists() else set()
    for run in candidate_runs(now):
        if run > now - dt.timedelta(hours=5):
            continue  # too early for any track file
        rid = run.strftime("%Y%m%d%H")
        log(f"run {rid}")
        if rid in existing:
            status.update(ok=True, message=f"Newest run {rid} processed")
            log("  already processed")
            break
        ens_url = find_tf_url(run, "ifs", "enfo")
        if not ens_url:
            continue  # not out yet (or no storms anywhere) - try the run before
        run_storms, urls = {}, {}
        with tempfile.TemporaryDirectory() as td:
            for prod in PRODUCTS:
                if prod["key"] == "ifs-hres" and "ifs-hres" in run_storms:
                    continue  # already taken from the ensemble file
                url = ens_url if prod["key"] == "ifs-ens" else find_tf_url(run, prod["model"], prod["stream"])
                if not url:
                    continue
                blob = http_get(url, timeout=120)
                if not blob:
                    continue
                fp = Path(td) / f"{prod['key']}.bufr"
                fp.write_bytes(blob)
                _, storms = decode_file(str(fp), prod["key"])
                if not add_product(run_storms, prod["key"], storms):
                    continue
                urls[prod["key"]] = url
                log(f"  {prod['key']}: {len(storms)} storms from {url}")
        write_run(out, run, merge(run_storms), urls)
        status.update(ok=True, message=f"Newest run {rid} processed")
        break
    else:
        status["message"] = "No track file found in the last 3 days (no active storms, or the server could not be reached)"
    update_index(out, status)


if __name__ == "__main__":
    main()
