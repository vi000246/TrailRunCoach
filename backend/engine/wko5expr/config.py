# PUBLIC-MIRROR-STUB (SP-500) — 核心未包含（公開版）
"""Part of the calculation core, which the public mirror does not include.
Every name here is a stand-in that raises CoreNotIncluded when used
(backend/engine/core_stub.py)."""
from backend.engine.core_stub import module_getattr as _module_getattr

CORE_STUB = True
__getattr__ = _module_getattr(__name__)
