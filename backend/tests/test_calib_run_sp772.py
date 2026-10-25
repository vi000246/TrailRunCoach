"""SP-772: GET /calib/run (the settings page polls it for the manual re-fit) answered 500 — run_status
used advanced_params (SP-740's 預設背負 line) without importing it."""
from __future__ import annotations

from backend.engine import calibrate as CAL
from backend.tests.test_calibrate import _item, _run, _session, registry  # noqa: F401 — fixture


def test_run_status_answers_with_no_job_and_the_items(registry):  # noqa: F811
    registry["a"] = _item(lambda ds, today: CAL.Fit(2.0, 0.1, 10), name="a")
    from backend.api import calib as API

    async def go():
        r = await API.run_status(db=await _session())
        assert r["job"] is None
        assert "a" in [x["name"] for x in r["items"]]                   # every registered parameter
    _run(go())
