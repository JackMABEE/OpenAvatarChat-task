"""Latency-filler controller for the LLM handler (Task 1 — Shape A).

Pure timer arm/disarm logic with no torch / audio / avatar imports so it can be
unit-tested on any host. The integration in ``llm_handler_openai_compatible.py``
wires ``emit_callback`` to a thread-safe push of a tiny ``DataBundle`` into the
per-turn AVATAR_TEXT stream; that side is what needs the GPU host to verify.

Contract
--------
* ``arm()`` schedules a timer for ``delay_ms``. Calling it again is a no-op.
* ``disarm()`` is called on each of the three exits in ``handle()``:
  (1) the first non-empty LLM chunk arrives,
  (2) the per-turn stream is cancelled (barge-in / STREAM_CANCEL),
  (3) the LLM call raises.
  It is idempotent and safe to call from any thread.
* The filler fires *at most once* per controller instance.
* ``emit_callback`` runs on the timer thread; the integration wraps it in
  ``loop.call_soon_threadsafe`` so the actual stream push happens on the emit
  loop, mirroring ``client_handler_rtc.flush_output``.

Disabled cases
--------------
``delay_ms <= 0`` or empty ``text`` -> ``enabled`` is False and ``arm()`` is a
no-op. Lets the config disable the feature without touching call sites.
"""

import threading
from typing import Callable, Optional


# (delay_seconds, callback) -> object with .start()/.cancel() and a `daemon` attr.
# threading.Timer satisfies this. Tests inject a fake to control firing.
TimerFactory = Callable[[float, Callable[[], None]], object]


class FillerController:
    def __init__(
        self,
        delay_ms: int,
        text: str,
        emit_callback: Callable[[str], None],
        timer_factory: Optional[TimerFactory] = None,
    ):
        self.delay_ms = delay_ms
        self.text = text
        self._emit_callback = emit_callback
        self._timer_factory = timer_factory or threading.Timer
        self._lock = threading.Lock()
        self._fired = False
        self._disarmed = False
        self._timer = None

    @property
    def enabled(self) -> bool:
        return self.delay_ms > 0 and bool(self.text)

    def arm(self) -> None:
        if not self.enabled:
            return
        with self._lock:
            if self._fired or self._disarmed or self._timer is not None:
                return
            timer = self._timer_factory(self.delay_ms / 1000.0, self._fire)
            try:
                timer.daemon = True
            except Exception:
                pass
            self._timer = timer
        timer.start()

    def disarm(self) -> None:
        with self._lock:
            self._disarmed = True
            timer = self._timer
            self._timer = None
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                pass

    def _fire(self) -> None:
        with self._lock:
            if self._fired or self._disarmed:
                return
            self._fired = True
        try:
            self._emit_callback(self.text)
        except Exception:
            # Timer thread must never crash the process; the LLM-handler thread
            # is the one that surfaces errors via its own except path.
            pass
