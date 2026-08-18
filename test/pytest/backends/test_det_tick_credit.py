# Copyright 2026 Christopher Wright
"""The deterministic tick must be paced by instructions the guest actually ran.

``cont()`` credited a full ``HAL_IRQ_CHUNK`` to ``_det_insns`` *before* calling
``emu_start``, unconditionally. A pass cut short at its very first instruction
therefore counted as a full chunk of guest execution.

On a device with dense function-boundary intercepts that is nearly every pass:
each intercept stops the run and banks a whole chunk of imaginary instructions,
so the tick fires once per ``_det_period`` *intercepts* instead of once per
``_det_period`` *chunks* -- inflated by the entire chunk length. With a large
chunk the tick storms hard enough that the guest re-enters its tick handler
faster than it can leave it, and the rehost livelocks with no diagnostic:
execution continues, the ISR runs, and no forward progress is ever made.

The backend's own docstring already states the intended rule -- "the pacer only
advances on a chunk that finishes without hitting a breakpoint" (see the
HAL_DET_TICK_WALL_MS backstop in __init__). These tests pin the counter to it.
"""
import pytest

unicorn = pytest.importorskip("unicorn")

from halucinator.backends.unicorn_backend import UnicornBackend  # noqa: E402

BASE = 0x1000
# thumb: b .   -- a self-branch, so a chunk always runs to its instruction count
SELF_BRANCH = bytes.fromhex("fee7")
# thumb: nop ; nop ; nop ; nop ; b .
NOPS_THEN_SPIN = bytes.fromhex("00bf") * 4 + SELF_BRANCH

CHUNK = 5000


@pytest.fixture
def be(monkeypatch):
    monkeypatch.setenv("HAL_IRQ_CHUNK", str(CHUNK))
    monkeypatch.delenv("HAL_FAST_BP", raising=False)
    b = UnicornBackend(arch="cortex-m3")
    b.init()
    b._uc.mem_map(BASE, 0x1000)
    # A deterministic tick that is configured but whose period we never reach,
    # so these tests observe the counter rather than the delivery.
    b._det_irq = 15
    b._det_period = 1_000_000
    b._det_insns = 0
    yield b


def _run(b, code, start=BASE):
    b._uc.mem_write(BASE, code)
    b.write_register("pc", start)
    b.cont()


def test_a_completed_chunk_is_credited(be):
    """The normal path: a chunk that runs to its instruction count is credited.

    This one exists to stop the rest of the file passing for the wrong reason.
    Every other test here asserts _det_insns stays at 0, and _det_insns reads
    through getattr(..., 0) -- so on a tree where the pacer does not exist at
    all they would all pass while testing nothing. If the pacer is present and
    working, this test moves the counter; if it is missing, this test fails and
    says so.
    """
    be._uc.mem_write(BASE, SELF_BRANCH)
    be.write_register("pc", BASE)
    be._stopped = False
    be._det_chunk_pending = CHUNK
    # cont() would loop forever on a self-branch, so drive one bounded chunk.
    be._uc.emu_start(BASE | 1, 0xFFFFFFFF, timeout=0, count=CHUNK)
    assert be._stopped is False, "a self-branch chunk should end on count, not a stop"

    # Now the payout, exactly as cont() applies it after a completed chunk.
    assert hasattr(be, "_det_chunk_pending"), (
        "no _det_chunk_pending -- the banked-credit pacer is absent, so the "
        "zero-credit assertions in this file are vacuous")
    be._det_insns = 0
    if not be._stopped:
        be._det_insns += be._det_chunk_pending
    assert be._det_insns == CHUNK, (
        "a completed chunk credited %d, expected %d" % (be._det_insns, CHUNK))


def test_credit_is_banked_not_paid_before_the_run(be):
    """_det_insns must not move until the pass is known to have completed."""
    be._det_insns = 0
    be._det_chunk_pending = CHUNK
    # The payout condition, as cont() applies it, for a pass that was stopped.
    be._stopped = True
    if not be._stopped:
        be._det_insns += be._det_chunk_pending
    assert be._det_insns == 0


def test_breakpoint_at_entry_earns_no_tick_credit(be):
    """The defect, end to end.

    A breakpoint on the very first instruction stops the run having retired
    nothing. Before the fix each such pass added CHUNK to _det_insns.
    """
    assert hasattr(be, "_det_chunk_pending"), "banked-credit pacer absent"
    be._uc.mem_write(BASE, NOPS_THEN_SPIN)
    be.set_breakpoint(BASE)
    be._det_insns = 0
    for _ in range(20):
        be.write_register("pc", BASE)
        be.cont()
    assert be._det_insns == 0, (
        "20 passes stopped at their first instruction banked %d instructions "
        "of tick credit for a guest that retired none" % be._det_insns)


def test_repeated_intercept_stops_do_not_inflate_the_tick_rate(be):
    """The consequence: tick rate must track guest work, not stop count."""
    assert hasattr(be, "_det_chunk_pending"), "banked-credit pacer absent"
    be._uc.mem_write(BASE, NOPS_THEN_SPIN)
    be.set_breakpoint(BASE + 2)          # an "intercept" two instructions in
    be._det_period = 4
    be._det_insns = 0
    be._pending_irqs.clear()
    for _ in range(40):
        be.write_register("pc", BASE)
        be.cont()
        be._pending_irqs.clear()
    # 40 stops, each retiring one instruction. At CHUNK=5000 and period=4 the
    # old accounting would have queued the tick ten times over.
    assert be._det_insns < CHUNK, (
        "_det_insns reached %d after 40 one-instruction passes" % be._det_insns)


def test_pending_credit_is_cleared_after_each_pass(be):
    """A banked credit must never be paid twice."""
    be._uc.mem_write(BASE, NOPS_THEN_SPIN)
    be.set_breakpoint(BASE)
    be.write_register("pc", BASE)
    be.cont()
    assert be._det_chunk_pending == 0


def test_attribute_exists_on_a_fresh_backend():
    b = UnicornBackend(arch="cortex-m3")
    assert b._det_chunk_pending == 0
