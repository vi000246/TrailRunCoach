"""
Sample GoldenCheetah OpenData (OSF 6hfpz, CC0 1.0; docs/research/public-datasets.md §3.1)
for the drift validation (docs/research/validation-goldencheetah.md).

The data never goes into the repo: everything is written under --root
(default C:/Users/<you>/Datasets/goldencheetah). Steps, each resumable:

    python -m backend.scripts.validation.gc_fetch list       # OSF listing -> manifest.json (name, size, md5, sha256)
    python -m backend.scripts.validation.gc_fetch scan       # per zip, HTTP range reads of the zip's central
                                                             # directory + the athlete JSON only -> scan.jsonl
    python -m backend.scripts.validation.gc_fetch download   # whole zips of the selected athletes, sha256-checked

scan reads only the metadata (each zip's JSON: sport, channels present, duration);
an athlete is selected when it has >= --min-runs runs of >= 40 min with HR and
power or distance. download stops at --cap-gb (scan bytes included).

Attribution: GoldenCheetah OpenData project, https://osf.io/6hfpz/ ,
DOI 10.17605/OSF.IO/6HFPZ (CC0 1.0 — no attribution required, cited anyway).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import os
import random
import re
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

API = "https://api.osf.io/v2/nodes/6hfpz/files/osfstorage/?page[size]=100"
UA = {"User-Agent": "TrailRunCoach-research/1.0 (drift validation; CC0 GoldenCheetah OpenData)"}
DEFAULT_ROOT = Path.home() / "Datasets" / "goldencheetah"
MIN_RUN_S = 2400                       # >= 40 min recorded (the brief's run length)
RUN_WORDS = re.compile(r"\b(run|running|lauf|laufen|carrera|course|corsa|corrida|hardloop|löp|juoks|bieg|beh|běh)",
                       re.I)
NOT_RUN = {"bike", "swim", "walk", "row", "ski", "xc ski", "hike", "strength", "gym", "elliptical"}


def _get(url: str, headers: dict | None = None, tries: int = 5, timeout: int = 120) -> bytes:
    err = None
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={**UA, **(headers or {})})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:   # noqa: PERF203
            err = e
            time.sleep(3 * (k + 1))
    raise RuntimeError(f"GET failed after {tries}: {url}: {err}")


class HttpFile(io.RawIOBase):
    """A seekable read-only file over HTTP range requests (OSF -> Google Cloud Storage, Accept-Ranges: bytes)."""

    TAIL = 1 << 16

    def __init__(self, url: str, size: int):
        self.url, self.size, self.pos, self.nbytes = url, size, 0, 0
        # one request through osf.io (~3 s each): it redirects to a signed storage URL, reused for the rest;
        # the zip's last 64 KB (end record + most central directories) come with it
        a = max(0, size - self.TAIL)
        req = urllib.request.Request(url, headers={**UA, "Range": f"bytes={a}-{size - 1}"})
        for k in range(5):
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    self.tail, self.url = r.read(), r.geturl()
                break
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
                if k == 4:
                    raise
                time.sleep(3 * (k + 1))
        self.tail_at = a
        self.nbytes += len(self.tail)

    def seekable(self):
        return True

    def readable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, off, whence=0):
        self.pos = off if whence == 0 else (self.pos + off if whence == 1 else self.size + off)
        return self.pos

    def read(self, n=-1):
        if n is None or n < 0:
            n = self.size - self.pos
        if n == 0 or self.pos >= self.size:
            return b""
        end = min(self.size, self.pos + n) - 1
        if self.pos >= self.tail_at:
            b = self.tail[self.pos - self.tail_at:end + 1 - self.tail_at]
            self.pos += len(b)
            return b
        b = _get(self.url, {"Range": f"bytes={self.pos}-{end}"})
        self.nbytes += len(b)
        self.pos += len(b)
        return b

    def readinto(self, buf):
        b = self.read(len(buf))
        buf[:len(b)] = b
        return len(b)


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

def cmd_list(root: Path) -> None:
    out, url, page = [], API, 0
    while url:
        d = json.loads(_get(url))
        page += 1
        for x in d["data"]:
            a = x["attributes"]
            if a.get("kind") != "file":
                continue
            h = (a.get("extra") or {}).get("hashes") or {}
            out.append({"name": a["name"], "id": x["id"], "size": a["size"], "md5": h.get("md5"),
                        "sha256": h.get("sha256"), "date_created": a.get("date_created"),
                        "download": x["links"]["download"]})
        url = d["links"].get("next")
        print(f"page {page}: {len(out)} files", flush=True)
    m = {"source": "https://osf.io/6hfpz/", "doi": "10.17605/OSF.IO/6HFPZ", "license": "CC0 1.0 Universal",
         "listed_at": dt.datetime.now(dt.timezone.utc).isoformat(), "files": out}
    (root / "manifest.json").write_text(json.dumps(m, indent=1), encoding="utf-8")
    tot = sum(f["size"] for f in out)
    print(f"{len(out)} files, {tot / 1e9:.1f} GB, sha256 on {sum(1 for f in out if f['sha256'])}")


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------

def _f(v):
    if isinstance(v, list):
        v = v[0] if v else None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def is_run_sport(sport: str) -> bool:
    s = (sport or "").strip().lower()
    if s == "run":
        return True
    if s in NOT_RUN:
        return False
    return bool(RUN_WORDS.search(s))


def _csv_time(name: str):
    try:
        return dt.datetime.strptime(name[:-4], "%Y_%m_%d_%H_%M_%S")
    except ValueError:
        return None


def match_csvs(rides: list[dict], names: list[str]) -> dict[int, str]:
    """{ride index: csv name}. The CSV is named by the local start time, the ride's
    `date` is UTC: the per-athlete offset is a whole quarter hour within ±14 h;
    the modal offset wins."""
    csv = [(n, _csv_time(n)) for n in names if n.endswith(".csv")]
    csv = [(n, t) for n, t in csv if t is not None]
    rt = []
    for r in rides:
        try:
            rt.append(dt.datetime.strptime(r["date"].replace(" UTC", ""), "%Y/%m/%d %H:%M:%S"))
        except (KeyError, ValueError):
            rt.append(None)
    by_t = {}
    for n, t in csv:
        by_t.setdefault(t, n)
    offs: dict[int, int] = {}
    for t in rt[:400]:
        if t is None:
            continue
        for q in range(-56, 57):
            if t + dt.timedelta(minutes=15 * q) in by_t:
                offs[q] = offs.get(q, 0) + 1
    order = sorted(offs, key=lambda q: -offs[q])
    out = {}
    for i, t in enumerate(rt):
        if t is None:
            continue
        for q in order[:3]:
            n = by_t.get(t + dt.timedelta(minutes=15 * q))
            if n:
                out[i] = n
                break
    return out


def scan_one(f: dict) -> dict:
    hf = HttpFile(f["download"], f["size"])
    rec = {"name": f["name"], "size": f["size"]}
    try:
        z = zipfile.ZipFile(io.BufferedReader(hf, buffer_size=1 << 20))
        names = z.namelist()
        js = [n for n in names if n.endswith(".json")]
        if not js:
            rec.update(err="no json", scan_bytes=hf.nbytes)
            return rec
        d = json.loads(z.read(js[0]))
    except Exception as e:      # noqa: BLE001 — one bad zip must not stop the scan
        rec.update(err=f"{type(e).__name__}: {e}"[:200], scan_bytes=hf.nbytes)
        return rec
    rides = d.get("RIDES") or []
    ath = d.get("ATHLETE") or {}
    m = match_csvs(rides, names)
    sports: dict[str, int] = {}
    runs = []
    for i, r in enumerate(rides):
        sp = (r.get("sport") or "").strip()
        sports[sp] = sports.get(sp, 0) + 1
        if not is_run_sport(sp):
            continue
        M = r.get("METRICS") or {}
        flags = r.get("data") or ""
        has_hr = (len(flags) > 4 and flags[4] == "H") or (_f(M.get("average_hr")) or 0) > 0
        has_p = (len(flags) > 3 and flags[3] == "P") or (_f(M.get("average_power")) or 0) > 0
        has_d = (len(flags) > 1 and flags[1] == "D") or (_f(M.get("total_distance")) or 0) > 0
        has_a = len(flags) > 7 and flags[7] == "A"
        wt = _f(M.get("workout_time")) or _f(M.get("time_recording")) or 0
        if not (has_hr and (has_p or has_d) and wt >= MIN_RUN_S and i in m):
            continue
        runs.append({"csv": m[i], "date": r.get("date"), "sport": sp, "flags": flags, "power": has_p, "alt": has_a,
                     "secs": wt, "km": _f(M.get("total_distance")), "gain": _f(M.get("elevation_gain")),
                     "avg_hr": _f(M.get("average_hr")), "avg_speed": _f(M.get("average_speed")),
                     "avg_power": _f(M.get("average_power")), "weight": _f(M.get("athlete_weight"))})
    rec.update(athlete=ath.get("id"), gender=ath.get("gender"), yob=ath.get("yob"), n_rides=len(rides),
               n_csv=sum(1 for n in names if n.endswith(".csv")), sports=sports, runs=runs, scan_bytes=hf.nbytes)
    return rec


def cmd_scan(root: Path, max_files: int, threads: int, seed: int, min_runs: int, enough: int) -> None:
    man = json.loads((root / "manifest.json").read_text(encoding="utf-8"))["files"]
    out = root / "scan.jsonl"
    done = set()
    good = 0
    nbytes = 0
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            done.add(r["name"])
            nbytes += r.get("scan_bytes", 0)
            good += len(r.get("runs") or []) >= min_runs
    files = [f for f in man if f["size"] > 2000 and f["name"] not in done]
    random.Random(seed).shuffle(files)
    files = files[:max(0, max_files - len(done))]
    print(f"already {len(done)} scanned ({good} selected); scanning {len(files)} more", flush=True)
    lock = threading.Lock()
    t0 = time.time()
    with open(out, "a", encoding="utf-8") as fo, ThreadPoolExecutor(threads) as ex:
        futs = {ex.submit(scan_one, f): f for f in files}
        for k, fu in enumerate(as_completed(futs), 1):
            r = fu.result()
            with lock:
                fo.write(json.dumps(r) + "\n")
                fo.flush()
                nbytes += r.get("scan_bytes", 0)
                good += len(r.get("runs") or []) >= min_runs
            if k % 50 == 0:
                print(f"{k}/{len(files)} scanned, {good} athletes with >= {min_runs} runs, "
                      f"{nbytes / 1e6:.0f} MB read, {time.time() - t0:.0f} s", flush=True)
            if good >= enough:
                print(f"enough: {good} athletes", flush=True)
                for g in futs:
                    g.cancel()
                break


# ---------------------------------------------------------------------------
# download
# ---------------------------------------------------------------------------

def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def cmd_download(root: Path, cap_gb: float, min_runs: int, max_athletes: int) -> None:
    man = {f["name"]: f for f in json.loads((root / "manifest.json").read_text(encoding="utf-8"))["files"]}
    scan = [json.loads(x) for x in (root / "scan.jsonl").read_text(encoding="utf-8").splitlines()]
    scan_bytes = sum(r.get("scan_bytes", 0) for r in scan)
    sel = [r for r in scan if len(r.get("runs") or []) >= min_runs]
    # most qualifying runs per MB first (a run with power counts 5×: the VI / Pw:HR checks need power):
    # the sample is about runs, not about rides
    sel.sort(key=lambda r: -(len(r["runs"]) + 4 * sum(x["power"] for x in r["runs"])) / max(1.0, r["size"] / 1e6))
    zdir = root / "zips"
    zdir.mkdir(exist_ok=True)
    budget = cap_gb * 1e9 - scan_bytes
    log = root / "download_log.jsonl"
    got, used = 0, 0
    with open(log, "a", encoding="utf-8") as fo:
        for r in sel:
            if got >= max_athletes:
                break
            f = man[r["name"]]
            p = zdir / f["name"]
            if p.exists() and _sha256(p) == f["sha256"]:
                got += 1
                used += f["size"]
                continue
            if used + f["size"] > budget:
                continue
            b = _get(f["download"], timeout=600)
            ok = hashlib.sha256(b).hexdigest() == f["sha256"] and hashlib.md5(b).hexdigest() == f["md5"]
            if ok:
                p.write_bytes(b)
                got += 1
                used += len(b)
            fo.write(json.dumps({"name": f["name"], "size": len(b), "sha256_ok": ok,
                                 "at": dt.datetime.now(dt.timezone.utc).isoformat()}) + "\n")
            fo.flush()
            print(f"{got} athletes, {used / 1e9:.2f} GB ({'ok' if ok else 'CHECKSUM MISMATCH'} {f['name']})",
                  flush=True)
    print(f"done: {got} zips, {used / 1e9:.2f} GB downloaded + {scan_bytes / 1e9:.2f} GB scan reads")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("list", "scan", "download"))
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--max-files", type=int, default=7000)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--seed", type=int, default=20261002)
    ap.add_argument("--min-runs", type=int, default=5)
    ap.add_argument("--enough", type=int, default=400, help="scan stops at this many selected athletes")
    ap.add_argument("--cap-gb", type=float, default=9.5)
    ap.add_argument("--max-athletes", type=int, default=400)
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    root = Path(a.root)
    root.mkdir(parents=True, exist_ok=True)
    if a.cmd == "list":
        cmd_list(root)
    elif a.cmd == "scan":
        cmd_scan(root, a.max_files, a.threads, a.seed, a.min_runs, a.enough)
    else:
        cmd_download(root, a.cap_gb, a.min_runs, a.max_athletes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
