"""
Repeated-route detection from GPS tracks.

Repeating the same route is the strongest progress evidence there is — every
coach in the research accepts a repeated benchmark — and this athlete repeats
more than it seems (one trail route 12 times in two years).

Each track is reduced to the set of ~100 m grid cells it passes through; two
activities are the same route when those sets overlap strongly (Jaccard). This
ignores direction and small detours, handles out-and-back versus loop only as
well as the overlap allows, and needs no map data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

CELL_DEG = 0.001      # ~111 m of latitude; ~100 m of longitude at 25 N


def cells(lat: Sequence[Optional[float]], lon: Sequence[Optional[float]],
          cell_deg: float = CELL_DEG) -> frozenset:
    return frozenset((int(a // cell_deg), int(b // cell_deg))
                     for a, b in zip(lat, lon) if a is not None and b is not None)


def jaccard(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass
class Route:
    id: int
    members: list = field(default_factory=list)     # activity keys, oldest first
    cells: frozenset = frozenset()                  # the first member's footprint

    def __len__(self) -> int:
        return len(self.members)


def cluster_routes(tracks: Iterable[tuple], threshold: float = 0.5) -> list[Route]:
    """Greedy clustering of (key, cells) pairs, in the order given (pass oldest
    first). An activity joins the route whose reference footprint it overlaps
    most, if that overlap is at least `threshold`."""
    routes: list[Route] = []
    for key, fp in tracks:
        if not fp:
            continue
        best, best_j = None, 0.0
        for r in routes:
            j = jaccard(fp, r.cells)
            if j > best_j:
                best, best_j = r, j
        if best is not None and best_j >= threshold:
            best.members.append(key)
        else:
            routes.append(Route(id=len(routes) + 1, members=[key], cells=fp))
    return routes
