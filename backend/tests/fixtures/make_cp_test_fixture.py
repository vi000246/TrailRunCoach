"""Generate the synthetic 3′ + 12′ CP-test fixture (cp_test_synthetic.json.gz).

A made-up 範例跑者 session with the shape of a COROS 3′/12′ test that fails the
two-point model and falls back to the single bout:

    9′ warm-up (150 → 185 W)  · 3′ at 234 W, HR peak ~150
    16′ easy recovery (~115 W) · 12′ at 238 W, HR peak ~174, even pacing
    ~8.5′ cool-down (~105 W)

so 3′ < 12′, the 3′ HR peak is ~24 bpm below the 12′ peak and the recovery is
< 25 min: cp_protocols.result() gives 1pt_prior, CP = 238 − 13 100 / 720 ≈ 220 W
(214–225), 參考. 1-s power (W) and heart rate (bpm) only, deterministic (seed
20250101, gzip mtime 0): rerunning writes the same bytes.

    python -m backend.tests.fixtures.make_cp_test_fixture
"""
import gzip
import io
import json
from pathlib import Path

import numpy as np

OUT = Path(__file__).with_name("cp_test_synthetic.json.gz")
SEED = 20250101

# (seconds, start W, end W, HR target) — power ramps linearly, HR chases its target
SEGMENTS = [
    (540, 150.0, 185.0, 138.0),   # warm-up
    (180, 234.0, 234.0, 152.0),   # 3′ bout
    (960, 120.0, 110.0, 118.0),   # recovery (16 min)
    (720, 238.0, 238.0, 176.0),   # 12′ bout
    (511, 110.0, 100.0, 112.0),   # cool-down
]
BOUTS = (1, 3)                    # segment indexes whose mean power is pinned exactly
HR_TAU_S = 70.0                   # first-order HR response
HR0 = 95.0


def build() -> dict:
    rng = np.random.default_rng(SEED)
    power, target = [], []
    for i, (n, a, b, hr) in enumerate(SEGMENTS):
        noise = rng.normal(0.0, 6.0, n)
        if i in BOUTS:
            noise -= noise.mean()                     # the bout averages exactly its target
        power.append(np.linspace(a, b, n) + noise)
        target.append(np.full(n, hr))
    p = np.concatenate(power)
    tgt = np.concatenate(target)
    h = np.empty_like(tgt)
    x = HR0
    for k, v in enumerate(tgt):
        x += (v - x) / HR_TAU_S
        h[k] = x
    h += rng.normal(0.0, 0.6, len(h))
    p_int = [int(round(v)) for v in np.clip(p, 0, None)]
    h_int = [int(round(v)) for v in h]
    return {"note": "Synthetic 範例跑者 3'/12' CP test (backend/tests/fixtures/make_cp_test_fixture.py, "
                    f"seed {SEED}): 1-s power (W) and heart rate (bpm) only.",
            "n": len(p_int), "power_w": p_int, "heart_rate_bpm": h_int}


def encode(d: dict) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as f:
        f.write(json.dumps(d, separators=(",", ":")).encode("utf-8"))
    return buf.getvalue()


if __name__ == "__main__":
    OUT.write_bytes(encode(build()))
    print(OUT, OUT.stat().st_size, "bytes")
