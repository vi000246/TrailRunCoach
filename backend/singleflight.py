"""
Single flight (SP-362): one computation per key at a time.

The slow synchronous computations behind the 課表 / 總覽 pages (api/overview._status, 9–13 s
cold on the NAS; api/plan_sessions._compute_inputs, 3–4 s) are memoised by key, but before this
nothing stopped concurrent callers — the calendar, both /suggestions calls, the warm-up thread,
an automatic plan run — from each computing the same key from scratch. With a flight, the first
caller computes and the others wait for its result.

    _FLIGHT = SingleFlight()
    value = _FLIGHT.do(key, compute)      # compute() runs once for concurrent callers of `key`

* Nothing is cached here: the callers keep their own caches (compute() stores into them).
* An exception reaches every caller waiting on that flight and is not remembered: the next
  call computes afresh (no poisoned cache).
* Thread-based (concurrent.futures.Future): the callers are sync functions run in the
  thread pool (async endpoints via run_in_threadpool) or in the warm-up thread. Never call
  do() on the event loop thread: a waiter blocks its thread.
* A compute() that asks for its own key on the same thread runs it inline (no self-deadlock).
"""
from __future__ import annotations

import threading
from concurrent.futures import Future
from typing import Any, Callable, Hashable


class SingleFlight:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._flights: dict[Hashable, tuple[Future, int]] = {}

    def do(self, key: Hashable, compute: Callable[[], Any]) -> Any:
        me = threading.get_ident()
        with self._lock:
            got = self._flights.get(key)
            if got is None:
                fut: Future = Future()
                self._flights[key] = (fut, me)
                leader = True
            else:
                fut, owner = got
                leader = False
        if not leader:
            if owner == me:                    # re-entered from inside its own compute()
                return compute()
            return fut.result()
        try:
            value = compute()
        except BaseException as e:
            fut.set_exception(e)
            raise
        else:
            fut.set_result(value)
            return value
        finally:
            with self._lock:
                if self._flights.get(key, (None,))[0] is fut:
                    del self._flights[key]

    def in_flight(self) -> int:
        """How many keys are being computed now (tests, logs)."""
        with self._lock:
            return len(self._flights)
