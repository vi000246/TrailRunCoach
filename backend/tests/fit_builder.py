"""Minimal FIT *writer* for tests — enough for fitparse to read a running
activity (file_id, records, session). The writer itself lives in
backend/demo/fitwrite.py (the demo athlete's generator extends it); this
module keeps the tests' import path. No personal data; values are synthetic."""
from __future__ import annotations

from backend.demo.fitwrite import (  # noqa: F401 — re-exported for the tests
    BYTES16, ENUM, FIT_EPOCH, STRYD_DEV_FIELDS, UINT8, UINT16, UINT32, _crc, _data, _definition,
    _dev_definition, _string, _stryd_dev_header, _ts, build_run, encode_activity,
)
