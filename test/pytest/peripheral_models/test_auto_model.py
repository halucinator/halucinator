"""
Tests for halucinator.peripheral_models.auto_model
(RecordingPeripheral and AutoPeripheral, plus the env-knob parsers).
"""
import pytest

from halucinator.peripheral_models.auto_model import (
    AutoPeripheral,
    RecordingPeripheral,
    parse_counter_addrs,
    parse_counter64_addrs,
    parse_counter_step,
    trace_db_path,
)
from halucinator.peripheral_models.generic import GenericPeripheral


# ===================== env-knob parsers =====================

class TestEnvParsers:

    def test_parse_counter_addrs_ok(self):
        addrs, bad = parse_counter_addrs("0x10, 0x20,0x30")
        assert addrs == {0x10, 0x20, 0x30}
        assert bad == []

    def test_parse_counter_addrs_reports_bad(self):
        addrs, bad = parse_counter_addrs("0x10,nope")
        assert addrs == {0x10}
        assert bad == ["nope"]

    def test_parse_counter_addrs_empty(self):
        assert parse_counter_addrs(None) == (set(), [])

    def test_parse_counter64_pair_explicit(self):
        pairs, bad = parse_counter64_addrs("0x200:0x204")
        assert pairs == {0x204: 0x200}  # hi -> lo
        assert bad == []

    def test_parse_counter64_pair_implicit_hi(self):
        pairs, _ = parse_counter64_addrs("0x300")
        assert pairs == {0x304: 0x300}  # bare lo implies hi = lo + 4

    def test_parse_counter_step_default(self):
        assert parse_counter_step(None) == 0x1000
        assert parse_counter_step("garbage") == 0x1000

    def test_parse_counter_step_value(self):
        assert parse_counter_step("0x40") == 0x40

    def test_trace_db_path_passthrough(self):
        assert trace_db_path("x.sqlite", environ={}) == "x.sqlite"

    def test_trace_db_path_suppressed(self):
        assert trace_db_path("x.sqlite",
                             environ={"HAL_NO_MMIO_TRACE": "1"}) is None


# ===================== RecordingPeripheral =====================

class TestRecordingPeripheral:

    def test_is_a_generic_peripheral(self):
        assert issubclass(RecordingPeripheral, GenericPeripheral)

    def test_reads_return_zero_and_record(self):
        p = RecordingPeripheral("rec", 0x40000000, 0x1000)
        assert p.hw_read(0x0, 4, pc=0x8000) == 0
        p.hw_write(0x4, 4, 0xdead, pc=0x8004)
        assert len(p.trace) == 2
        # (seq, pc, addr, size, value, rw)
        assert p.trace[0][5] == "r"
        assert p.trace[1] == (1, 0x8004, 0x40000004, 4, 0xdead, "w")


# ===================== AutoPeripheral =====================

class TestAutoPeripheral:

    def test_mro_is_recording_then_generic(self):
        mro = [c.__name__ for c in AutoPeripheral.__mro__]
        assert mro[:3] == ["AutoPeripheral", "RecordingPeripheral",
                           "GenericPeripheral"]

    def test_class_name_is_stable(self):
        # main._instantiate_peripheral name-checks "AutoPeripheral" for skip_svc;
        # renaming the class silently breaks that hook.
        assert AutoPeripheral.__name__ == "AutoPeripheral"

    def test_busywait_breaker_escalates_off_zero(self):
        p = AutoPeripheral("a", 0x40000000, 0x1000, stall_threshold=4)
        # First reads return 0; once the same (pc,addr) spins past the
        # threshold the breaker escalates to all-ones so a wait-for-SET exits.
        vals = [p.hw_read(0x0, 4, pc=0x8000) for _ in range(12)]
        assert vals[0] == 0
        assert 0xFFFFFFFF in vals

    def test_breaker_then_zero_tier(self):
        p = AutoPeripheral("a", 0x40000000, 0x1000, stall_threshold=4)
        seen = set(p.hw_read(0x0, 4, pc=0x8000) for _ in range(20))
        # both tiers observed: all-ones (wait-for-SET) and zero (wait-while-BUSY)
        assert 0xFFFFFFFF in seen
        assert 0 in seen

    def test_windowed_breaker_breaks_interleaved_poll(self):
        # A register polled in a loop that ALSO reads a second register every
        # iteration (A,B,A,B,...) never builds a strictly-consecutive run, so
        # the consecutive detector alone never fires (its run resets on every
        # B). The windowed detector must still break the spin on A within a
        # bounded number of reads. This is the gps-tracker UOTGHS/USB regression
        # (target register polled interleaved with another).
        p = AutoPeripheral("a", 0x40000000, 0x1000,
                           stall_threshold=8, stall_window=40, stall_win_div=4)
        # trigger = max(8, 40 // 4) = 10 reads of A within the window.
        a_vals = []
        for _ in range(20):
            a_vals.append(p.hw_read(0x0, 4, pc=0x8000))   # register A
            p.hw_read(0x4, 4, pc=0x8004)                  # interleaved reg B
        # The strict-consecutive counter never reached the threshold — the
        # interleaving keeps resetting it — proving the break came from the
        # windowed path, not the consecutive one.
        assert p._repeat[(0x8000, 0x40000000)] < p.stall_threshold
        # A broke: a wait-for-SET spin sees all-ones within the bounded loop...
        assert 0xFFFFFFFF in a_vals
        # ...and promptly (once its dominance threshold of 10 reads is crossed).
        assert a_vals.index(0xFFFFFFFF) <= 12

    def test_interleaved_poll_not_broken_when_not_dominant(self):
        # Conservative-behaviour guard: a register read only occasionally (not
        # spun on) must NOT be broken by the windowed detector, even over many
        # reads. Here A is read once per 8 reads of B — well under the 1/4
        # dominance bar — so every A read must still return 0.
        p = AutoPeripheral("a", 0x40000000, 0x1000,
                           stall_threshold=8, stall_window=40, stall_win_div=4)
        a_vals = []
        for _ in range(30):
            a_vals.append(p.hw_read(0x0, 4, pc=0x8000))       # register A (rare)
            for _ in range(7):
                p.hw_read(0x4, 4, pc=0x8004)                  # register B (busy)
        assert set(a_vals) == {0}

    def test_write_clears_stall_state(self):
        p = AutoPeripheral("a", 0x40000000, 0x1000, stall_threshold=4)
        for _ in range(6):
            p.hw_read(0x0, 4, pc=0x8000)
        p.hw_write(0x0, 4, 1, pc=0x8000)   # a write to the polled reg
        assert p._last_key is None

    def test_free_running_counter_monotonic(self):
        p = AutoPeripheral("c", 0x50000000, 0x1000,
                          counter_addrs=[0x50000000], counter_step=0x100)
        vals = [p.hw_read(0x0, 4, pc=0x9000) for _ in range(3)]
        assert vals == [0x100, 0x200, 0x300]

    def test_counter64_high_word_stable_across_reads(self):
        # hi must not advance on its own read, or a do/while(hi1!=hi2) loop
        # never converges.
        p = AutoPeripheral("c64", 0x60000000, 0x1000,
                          counter64_addrs={0x60000004: 0x60000000},
                          counter_step=0x1000)
        hi1 = p.hw_read(0x4, 4, pc=0x9000)
        hi2 = p.hw_read(0x4, 4, pc=0x9000)
        assert hi1 == hi2   # stable

    def test_output_capture_emits_line(self):
        p = AutoPeripheral("uart", 0x40004400, 0x400)
        for ch in b"HI\n":
            p.hw_write(0x0, 1, ch, pc=0x8000)
        # the newline flushes the buffer
        assert p._out.get(0x40004400) == bytearray()


# ===================== bare-name resolution in main =====================

class TestBareNameResolution:
    """`emulate: AutoPeripheral` (no dotted path) must resolve, via both
    resolvers in halucinator.main."""

    def test_instantiate_peripheral_resolves_bare_name(self):
        import types
        from halucinator import main
        mem = types.SimpleNamespace(name="mmio", base_addr=0x40000000,
                                    size=0x1000, properties=None)
        periph = main._instantiate_peripheral("AutoPeripheral", mem, None)
        assert periph is not None
        assert periph.__class__.__name__ == "AutoPeripheral"

    def test_instantiate_peripheral_bare_recording(self):
        import types
        from halucinator import main
        mem = types.SimpleNamespace(name="mmio", base_addr=0x40000000,
                                    size=0x1000, properties=None)
        periph = main._instantiate_peripheral("RecordingPeripheral", mem, None)
        assert periph.__class__.__name__ == "RecordingPeripheral"


# ===================== stall-detector logging + windowing =====================

class _CountingHandler:
    """Counts records the auto_model logger emits, without touching config."""

    def __init__(self):
        self.records = []

    def handle(self, record):
        self.records.append(record)

    # logging.Logger.handle() calls these on a handler object.
    level = 0

    def acquire(self): pass

    def release(self): pass

    def createLock(self): pass


@pytest.fixture
def hal_records(monkeypatch):
    from halucinator.peripheral_models import auto_model as am
    h = _CountingHandler()
    monkeypatch.setattr(am, "hlog", _Recorder(h))
    return h


class _Recorder:
    """Minimal stand-in for the HAL logger that just records info() calls."""

    def __init__(self, sink):
        self._sink = sink

    def info(self, fmt, *args):
        self._sink.records.append(fmt % args if args else fmt)

    def __getattr__(self, _name):
        return lambda *a, **k: None


class TestStallLogging:
    """Stall lines come out of the MMIO read hook, so one per read turned a
    long spin into millions of identical lines."""

    def test_consecutive_spin_logs_once_per_tier_not_per_read(self, hal_records):
        p = AutoPeripheral("mmio", 0x40000000, 0x100, stall_threshold=4)
        for _ in range(4000):
            p.hw_read(0x10, 4, pc=0x8000)
        assert len(hal_records.records) <= 3, (
            f"{len(hal_records.records)} log lines for one spinning register; "
            "expected at most one per escalation tier")
        assert hal_records.records, "the spin was never announced at all"

    def test_windowed_spin_logs_once_per_tier_not_per_read(self, hal_records):
        # Interleave two keys so the strict-consecutive run never builds:
        # this is exactly the case the windowed detector exists for.
        p = AutoPeripheral("mmio", 0x40000000, 0x100, stall_threshold=1000,
                           stall_window=64, stall_win_div=4)
        for _ in range(4000):
            p.hw_read(0x10, 4, pc=0x8000)
            p.hw_read(0x20, 4, pc=0x9000)
        assert len(hal_records.records) <= 6, (
            f"{len(hal_records.records)} log lines for two spinning registers")

    def test_the_read_that_reaches_the_trigger_is_not_discarded(self):
        """The window used to be cleared before the dominance test, so the read
        that rolled it over lost the escalation it had just earned — one read
        per window. (Counts restarting after a rollover is the tumbling window
        working as intended, and is not what this checks.)

        Sized for hand-checking: trigger = max(4, 8 // 4) = 4, one key, so
        reads 4..8 of each 8-read window dominate and the fifth is the
        rollover read.
        """
        p = AutoPeripheral("mmio", 0x40000000, 0x100, stall_threshold=4,
                           stall_window=8, stall_win_div=4)
        key = (0x8000, 0x40000010)
        for _ in range(8):
            p.hw_read(0x10, 4, pc=0x8000)
        assert p._win_escal.get(key, 0) == 5, (
            "expected reads 4,5,6,7,8 of the window to escalate; got "
            f"{p._win_escal.get(key, 0)} -- 4 means the rollover read was "
            "denied because the window was cleared before the check")
        # And the window really did roll over, so this was the boundary case.
        assert p._win_total == 0
