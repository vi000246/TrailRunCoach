"""
Data corrections — detect bad samples and blank them, with the athlete's
approval.

Nothing is auto-applied and the athlete's `.wko4` files are never modified:
corrections are stored as an overlay in ~/.wko5coach/corrections.json and
applied when channels are read. That keeps WKO5's own data intact (WKO5
rewrites those files on sync) and makes every correction reversible.

Spike detection reuses the logic of WKO5's own "Find Power Spikes & Bad Data"
charts, which only surface the problem for a human to eyeball:

    robust peak = avg(greatest(meanmax(power, 1), 5))   -- top 5 across workouts
    max peak    = max(meanmax(power, 1))

If a single sample stands far above the robust peak it is almost certainly a
sensor glitch rather than a real effort, because genuine peaks repeat.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

CORRECTIONS_PATH = None      # fixed file (tests); None = the tenant's corrections.json


def corrections_path() -> Path:
    if CORRECTIONS_PATH is not None:
        return Path(CORRECTIONS_PATH)
    from backend import tenancy
    return tenancy.base_path("corrections.json")


@dataclass
class Correction:
    """Blank `channel` over [t_start, t_end] seconds of one workout."""
    file: str
    channel: str
    t_start: float
    t_end: float
    action: str = "blank"
    reason: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def covers(self, t: float) -> bool:
        return self.t_start <= t <= self.t_end


class CorrectionStore:
    def __init__(self, path: Optional[Path] = None):
        self.path = path or corrections_path()
        self.items: list[Correction] = []
        self.load()

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            data = {}
        self.items = [Correction(**c) for c in data.get("corrections", [])]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"corrections": [asdict(c) for c in self.items]}, indent=2), "utf-8")

    def add(self, corrections: list[Correction]) -> list[Correction]:
        have = {(c.file, c.channel, c.t_start, c.t_end) for c in self.items}
        new = [c for c in corrections if (c.file, c.channel, c.t_start, c.t_end) not in have]
        self.items.extend(new)
        self.save()
        return new

    def remove(self, correction_id: str) -> bool:
        before = len(self.items)
        self.items = [c for c in self.items if c.id != correction_id]
        if len(self.items) != before:
            self.save()
            return True
        return False

    def for_file(self, file: str, channel: str) -> list[Correction]:
        # the same FIT file in the 同步資料 source ("coros/…" prefix) too; never another
        # source's file of the activity — a correction's times are offsets into its own file
        from backend.engine.activity_key import same_file
        return [c for c in self.items if same_file(c.file, file) and c.channel == channel]

    def apply(self, file: str, channel: str, times, values: list) -> list:
        """Return `values` with approved corrections applied (blanked)."""
        rules = self.for_file(file, channel)
        if not rules:
            return values
        out = list(values)
        for i, t in enumerate(times):
            if t is not None and any(r.covers(t) for r in rules):
                out[i] = None
        return out


# ---------------------------------------------------------------------------
# detection
# ---------------------------------------------------------------------------

def workout_peaks(ds, channel: str = "power") -> list[float]:
    """Highest sample of `channel` in each workout (disk-cached by Dataset)."""
    return list(ds.channel_peaks(channel).values())


def peak_baseline(ds, channel: str = "power", percentile: float = 90.0) -> Optional[float]:
    """A peak the athlete genuinely reaches: the `percentile`-th of their
    per-workout peaks.

    WKO5's own spike chart compares the single maximum against the average of
    the top 5, but that breaks down when several workouts are corrupted — the
    baseline is then pulled up by the very samples we are hunting. A high
    percentile of ALL workout peaks is robust to a handful of bad files.
    """
    peaks = sorted(workout_peaks(ds, channel))
    if not peaks:
        return None
    k = (len(peaks) - 1) * percentile / 100.0
    lo, hi = int(k), min(int(k) + 1, len(peaks) - 1)
    return peaks[lo] + (peaks[hi] - peaks[lo]) * (k - lo)


def detect_spikes(ds, channel: str = "power", factor: float = 1.6,
                  limit: Optional[float] = None, percentile: float = 90.0) -> list[dict]:
    """Propose corrections for samples above `factor` x the robust peak.

    Returns dicts with the evidence needed to decide: the workout, when, how
    many samples, the peak value, and what the workout's peak becomes after.
    """
    base = limit if limit is not None else peak_baseline(ds, channel, percentile)
    if not base:
        return []
    threshold = base * factor
    # Only workouts whose peak clears the threshold can contain a spike, and the
    # peaks are already cached — so we parse just those few files, not all 1000.
    peaks = ds.channel_peaks(channel)
    suspect = {f for f, pk in peaks.items() if pk > threshold}
    out = []
    for w in ds.workouts:
        if w.entry.file not in suspect:
            continue
        f = ds.wko4(w.idx)
        c = f.channels.get(channel) if f else None
        t = f.channels.get("elapsedtime") if f else None
        if not c or not t:
            continue
        vals = c.values
        if ds.corrections is not None:
            vals = ds.corrections.apply(w.entry.file, channel, t.values, vals)
        bad = [(ti, v) for ti, v in zip(t.values, vals)
               if v is not None and ti is not None and v > threshold]
        if not bad:
            continue
        kept = [v for ti, v in zip(t.values, vals)
                if v is not None and ti is not None and v <= threshold]
        out.append({
            "file": w.entry.file,
            "workout": w.idx,
            "start": w.entry.start.isoformat(),
            "sport": w.sport_type,
            "channel": channel,
            "t_start": min(b[0] for b in bad),
            "t_end": max(b[0] for b in bad),
            "samples": len(bad),
            "peak": max(b[1] for b in bad),
            "peak_after": max(kept) if kept else None,
            "threshold": threshold,
            "baseline": base,
            "reason": (f"{len(bad)} sample(s) above {threshold:.0f} "
                       f"({factor}x the p{percentile:.0f} workout peak of {base:.0f})"),
        })
    out.sort(key=lambda d: -d["peak"])
    return out


def proposals_to_corrections(proposals: list[dict]) -> list[Correction]:
    return [Correction(file=p["file"], channel=p["channel"], t_start=p["t_start"],
                       t_end=p["t_end"], reason=p.get("reason", "")) for p in proposals]
