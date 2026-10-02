"""地區 tw | intl (engine/region.py) for the pages (static/region.js)."""
from __future__ import annotations

from fastapi import APIRouter

from backend.engine import region as RG

router = APIRouter(prefix="/api/v1/region", tags=["region"])


@router.get("")
def get_region():
    reg, how = RG.region()
    return {"region": reg, "how": how, "label": RG.LABEL[reg], "basemap_default": RG.default_basemap(reg),
            "options": list(RG.REGIONS)}
