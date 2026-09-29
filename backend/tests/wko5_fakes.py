"""In-memory stand-ins for the WKO5 Dataset, for synthetic evaluator tests."""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Optional

import numpy as np

from backend.engine.wko5expr.dataset import Workout, date_to_day
from backend.files.wko4_file import Channel, Wko4File


@dataclass
class FakeWorkout:
    start: dt.datetime
    sport: str = "run"
    tags: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    channels: dict = field(default_factory=dict)  # name -> list of values (incl. elapsedtime)


class FakeDataset:
    """Duck-types backend.engine.wko5expr.dataset.Dataset."""

    def __init__(self, workouts: list[FakeWorkout], today: dt.date,
                 settings: Optional[dict] = None, ctl=42.0, atl=7.0):
        self.today = date_to_day(today)
        self.memo: dict = {}
        self.athlete = SimpleNamespace(
            ctlconstant=ctl, atlconstant=atl,
            settings={k: [(dt.date(1980, 1, 1), v)] for k, v in (settings or {}).items()},
            setting_on=lambda name, day: (settings or {}).get(name),
        )
        self._files: dict[int, Wko4File] = {}
        self.workouts: list[Workout] = []
        for i, fw in enumerate(sorted(workouts, key=lambda w: w.start)):
            entry = SimpleNamespace(start=fw.start, file=f"fake/{i}.wko4", ftp=None, metrics={}, record=None)
            m = {"tss": None, "if": None, "distance": None, "climbing": None, "duration": None,
                 "movingduration": None, "plannedtss": None}
            m.update(fw.metrics)
            w = Workout(idx=i, entry=entry, day=date_to_day(fw.start), sport=fw.sport,
                        sport_type=fw.sport, tags=[t.lower() for t in fw.tags], metrics=m)
            self.workouts.append(w)
            if fw.channels:
                chans = {name: Channel(name, list(vals), 1.0) for name, vals in fw.channels.items()}
                self._files[i] = Wko4File(path="", sport=fw.sport, start_time=fw.start.isoformat(),
                                          device=None, weight_kg=None, original_type=None,
                                          original_bytes=None, channels=chans, ranges=[], info=None)
        days = [math.floor(w.day) for w in self.workouts] or [int(self.today)]
        self.first_day, self.last_day = min(days), max(days)

    def setting(self, name, day):
        return self.athlete.setting_on(name, day)

    def sport_setting(self, kind, w):
        return self.setting(("run" if w.sport == "run" else "other") + kind, w.day)

    def wko4(self, idx):
        return self._files.get(idx)

    def channel(self, idx, name):
        f = self._files.get(idx)
        if f is None:
            return None
        t = f.channels.get("elapsedtime")
        if name == "deltatime":
            tv = np.array([np.nan if v is None else v for v in t.values], dtype=float)
            return np.diff(tv, prepend=0.0)
        c = f.channels.get(name)
        if c is None:
            return None
        return np.array([np.nan if v is None else v for v in c.values], dtype=float)
