"""Unit tests for FillerController (Task 1 — pure core, no GPU/audio deps)."""

import threading

import pytest

from handlers.llm.openai_compatible.filler_controller import FillerController


# ---------------------------------------------------------------------------
# Fake timer the tests drive manually. Lets us model "ASR-end -> first-chunk
# gap" by deciding whether the timer fires before disarm() is called.
# ---------------------------------------------------------------------------

class FakeTimer:
    def __init__(self, delay, callback):
        self.delay = delay
        self.callback = callback
        self.started = False
        self.cancelled = False
        self.daemon = False

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def fire(self):
        """Simulate the OS firing the timer after `delay` seconds."""
        # threading.Timer would also skip its callback after cancel(); mirror that.
        if self.cancelled:
            return
        self.callback()


def _factory():
    timers = []

    def make(delay, callback):
        t = FakeTimer(delay, callback)
        timers.append(t)
        return t

    return make, timers


# ---------------------------------------------------------------------------
# Enablement
# ---------------------------------------------------------------------------

def test_disabled_when_delay_zero():
    c = FillerController(0, "嗯", lambda _t: None)
    assert c.enabled is False


def test_disabled_when_delay_negative():
    c = FillerController(-1, "嗯", lambda _t: None)
    assert c.enabled is False


def test_disabled_when_text_empty():
    c = FillerController(600, "", lambda _t: None)
    assert c.enabled is False


def test_arm_is_noop_when_disabled():
    make, timers = _factory()
    c = FillerController(0, "嗯", lambda _t: None, timer_factory=make)
    c.arm()
    assert timers == []


# ---------------------------------------------------------------------------
# Gap < delay -> no emit;  Gap > delay -> exactly one emit.
# (We model "delay elapsed" by firing the FakeTimer; "first chunk arrived"
# by calling disarm(). The order between them is the simulated gap.)
# ---------------------------------------------------------------------------

def test_no_emit_when_first_chunk_arrives_before_delay():
    emits = []
    make, timers = _factory()
    c = FillerController(600, "嗯", emits.append, timer_factory=make)
    c.arm()
    assert len(timers) == 1 and timers[0].started

    # Simulated gap < delay: disarm before the timer ever fires.
    c.disarm()
    assert timers[0].cancelled is True

    # Even if the OS still managed to fire it post-cancel, FakeTimer mirrors
    # threading.Timer and skips the callback.
    timers[0].fire()
    assert emits == []


def test_emits_when_delay_elapses_before_first_chunk():
    emits = []
    make, timers = _factory()
    c = FillerController(600, "嗯", emits.append, timer_factory=make)
    c.arm()

    # Simulated gap > delay: timer fires first, then the first chunk arrives.
    timers[0].fire()
    assert emits == ["嗯"]

    # disarm() arriving *after* the fire is a safe no-op.
    c.disarm()
    assert emits == ["嗯"]


# ---------------------------------------------------------------------------
# Disarm on all three exits in handle(). The unit call shape is identical;
# we keep three named tests so the integration map is explicit.
# ---------------------------------------------------------------------------

def test_disarm_on_first_chunk_exit():
    emits = []
    make, timers = _factory()
    c = FillerController(600, "嗯", emits.append, timer_factory=make)
    c.arm()
    c.disarm()
    assert timers[0].cancelled is True and emits == []


def test_disarm_on_stream_cancel_exit():
    emits = []
    make, timers = _factory()
    c = FillerController(600, "嗯", emits.append, timer_factory=make)
    c.arm()
    c.disarm()
    assert timers[0].cancelled is True and emits == []


def test_disarm_on_exception_exit():
    emits = []
    make, timers = _factory()
    c = FillerController(600, "嗯", emits.append, timer_factory=make)
    c.arm()
    c.disarm()
    assert timers[0].cancelled is True and emits == []


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

def test_repeat_arm_creates_only_one_timer():
    make, timers = _factory()
    c = FillerController(600, "嗯", lambda _t: None, timer_factory=make)
    c.arm()
    c.arm()
    c.arm()
    assert len(timers) == 1


def test_repeat_disarm_is_safe():
    emits = []
    make, timers = _factory()
    c = FillerController(600, "嗯", emits.append, timer_factory=make)
    c.arm()
    for _ in range(10):
        c.disarm()
    assert timers[0].cancelled is True
    assert emits == []


def test_repeat_fire_emits_exactly_once():
    emits = []
    make, timers = _factory()
    c = FillerController(600, "嗯", emits.append, timer_factory=make)
    c.arm()
    timers[0].fire()
    timers[0].fire()  # OS-level double-fire would still hit _fire twice
    timers[0].fire()
    assert emits == ["嗯"]


def test_arm_after_disarm_does_not_re_arm():
    make, timers = _factory()
    c = FillerController(600, "嗯", lambda _t: None, timer_factory=make)
    c.arm()
    c.disarm()
    c.arm()  # should be a no-op — the turn is over
    assert len(timers) == 1


def test_arm_after_fire_does_not_re_arm():
    emits = []
    make, timers = _factory()
    c = FillerController(600, "嗯", emits.append, timer_factory=make)
    c.arm()
    timers[0].fire()
    c.arm()  # should be a no-op — already fired
    assert len(timers) == 1
    assert emits == ["嗯"]


# ---------------------------------------------------------------------------
# Thread safety: at-most-once under contention.
# ---------------------------------------------------------------------------

def test_concurrent_fires_emit_at_most_once():
    """20 threads call _fire simultaneously -> at most one emit."""
    emits = []
    emits_lock = threading.Lock()

    def emit(text):
        with emits_lock:
            emits.append(text)

    c = FillerController(600, "嗯", emit)

    barrier = threading.Barrier(20)

    def race():
        barrier.wait()
        c._fire()

    threads = [threading.Thread(target=race) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(emits) == 1


def test_concurrent_arm_disarm_fire_emits_at_most_once():
    """arm/disarm/fire all racing concurrently -> filler still fires <= 1 time."""
    emits = []
    emits_lock = threading.Lock()

    def emit(text):
        with emits_lock:
            emits.append(text)

    c = FillerController(600, "嗯", emit)

    barrier = threading.Barrier(30)

    def do_arm():
        barrier.wait()
        c.arm()

    def do_disarm():
        barrier.wait()
        c.disarm()

    def do_fire():
        barrier.wait()
        c._fire()

    threads = []
    threads += [threading.Thread(target=do_arm) for _ in range(10)]
    threads += [threading.Thread(target=do_disarm) for _ in range(10)]
    threads += [threading.Thread(target=do_fire) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(emits) <= 1


# ---------------------------------------------------------------------------
# emit_callback exceptions must not propagate out of the timer thread.
# ---------------------------------------------------------------------------

def test_emit_callback_exception_is_swallowed():
    def bad(_text):
        raise RuntimeError("boom")

    c = FillerController(600, "嗯", bad)
    c._fire()  # must not raise
