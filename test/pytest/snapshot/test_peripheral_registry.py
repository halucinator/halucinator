"""Layer-2 peripheral-model snapshot tests.

Covers the explicit per-model save/restore (uart/gpio/interrupts), the generic
deep-copy fallback + SnapshotableModel mixin, and the whole PeripheralRegistry
round-trip. The alias-preservation case (Interrupts.Active_Interrupts is active)
is the subtle correctness requirement.
"""
from collections import defaultdict, deque

import pytest

from halucinator.peripheral_models.gpio import GPIO
from halucinator.peripheral_models.interrupts import Interrupts
from halucinator.peripheral_models.uart import UARTPublisher
from halucinator.snapshot import (
    PeripheralRegistry,
    SnapshotableModel,
    default_restore,
    default_save,
)


@pytest.fixture(autouse=True)
def clean_state():
    """Reset the class-level peripheral state around every test so nothing
    leaks between tests (these are module-global dicts/deques)."""
    UARTPublisher.rx_buffers = defaultdict(deque)
    GPIO.gpio_state = defaultdict(int)
    Interrupts.active = defaultdict(bool)
    Interrupts.Active_Interrupts = Interrupts.active
    Interrupts.enabled = defaultdict(bool)
    yield


class TestExplicitModelSaveRestore:
    def test_uart_round_trip(self):
        UARTPublisher.rx_buffers[0].extend("hello")
        UARTPublisher.rx_buffers[1].extend("world")
        state = UARTPublisher.save_state()

        UARTPublisher.rx_buffers[0].clear()
        UARTPublisher.rx_buffers[2].extend("junk")

        assert UARTPublisher.restore_state(state) is True
        assert list(UARTPublisher.rx_buffers[0]) == list("hello")
        assert list(UARTPublisher.rx_buffers[1]) == list("world")
        assert 2 not in UARTPublisher.rx_buffers

    def test_uart_restore_preserves_buffer_alias(self):
        """A caller holding a reference to rx_buffers before restore should see
        the restored contents through that same reference (in-place restore)."""
        alias = UARTPublisher.rx_buffers
        UARTPublisher.rx_buffers[0].extend("abc")
        state = UARTPublisher.save_state()
        UARTPublisher.rx_buffers[0].clear()
        UARTPublisher.restore_state(state)
        assert alias is UARTPublisher.rx_buffers
        assert list(alias[0]) == list("abc")

    def test_gpio_round_trip(self):
        GPIO.gpio_state["PA0"] = 1
        GPIO.gpio_state["PB3"] = 1
        state = GPIO.save_state()

        GPIO.gpio_state["PA0"] = 0
        GPIO.gpio_state["PC7"] = 1

        assert GPIO.restore_state(state) is True
        assert GPIO.gpio_state["PA0"] == 1
        assert GPIO.gpio_state["PB3"] == 1
        assert "PC7" not in GPIO.gpio_state

    def test_interrupts_round_trip_and_alias(self):
        Interrupts.active[3] = True
        Interrupts.enabled[3] = True
        state = Interrupts.save_state()

        # Mutate, and confirm the alias tracks the mutation (they are one dict).
        Interrupts.active[3] = False
        Interrupts.active[9] = True
        assert Interrupts.Active_Interrupts[9] is True

        assert Interrupts.restore_state(state) is True
        assert Interrupts.active[3] is True
        assert 9 not in Interrupts.active
        # The alias MUST still be the very same object after restore.
        assert Interrupts.Active_Interrupts is Interrupts.active
        assert Interrupts.Active_Interrupts[3] is True


class TestGenericDefault:
    def test_default_save_restore_mutable_attrs(self):
        class Model:
            d = {}
            lst = []

        Model.d["k"] = 1
        Model.lst.append("x")
        state = default_save(Model)

        Model.d["k"] = 999
        Model.lst.append("y")

        assert default_restore(Model, state) is True
        assert Model.d == {"k": 1}
        assert Model.lst == ["x"]

    def test_default_restore_is_in_place(self):
        class Model:
            d = {"a": 1}

        alias = Model.d
        state = default_save(Model)
        Model.d["a"] = 2
        default_restore(Model, state)
        assert alias is Model.d  # not rebound
        assert alias["a"] == 1

    def test_snapshotable_mixin(self):
        class Model(SnapshotableModel):
            counters = {}

        Model.counters["hits"] = 5
        state = Model.save_state()
        Model.counters["hits"] = 0
        assert Model.restore_state(state) is True
        assert Model.counters["hits"] == 5

    def test_set_and_bytearray_restore_in_place(self):
        """A model whose state is a set / bytearray must restore through the
        set/bytearray in-place branches (not the setattr rebind), preserving
        aliases just like dict/list do."""
        class Model:
            flags = {1, 2, 3}
            buf = bytearray(b"orig")

        flags_alias, buf_alias = Model.flags, Model.buf
        state = default_save(Model)
        Model.flags.add(99)
        Model.flags.discard(1)
        Model.buf[:] = b"XXXX"

        assert default_restore(Model, state) is True
        assert Model.flags == {1, 2, 3}
        assert bytes(Model.buf) == b"orig"
        # in place: the same live objects, not rebound copies
        assert Model.flags is flags_alias
        assert Model.buf is buf_alias

    def test_restore_rebinds_when_container_type_changed(self):
        """If the live attribute is no longer a matching container (shape
        changed since capture), _restore_in_place returns False and
        default_restore falls back to setattr — the value is still restored,
        just rebound rather than mutated in place."""
        class Model:
            val = {"was": "dict"}

        state = default_save(Model)
        Model.val = ["now", "a", "list"]  # type changed -> rebind path
        assert default_restore(Model, state) is True
        assert Model.val == {"was": "dict"}

    def test_snapshotable_attrs_skips_raising_descriptor(self):
        """A property that raises on access must be skipped by the attribute
        walk, not abort the whole capture (the descriptor-guard branch)."""
        class Model:
            good = {"k": 1}

            @property
            def boom(self):  # noqa: D401 - test fixture
                raise RuntimeError("do not touch me")

        # default_save walks dir(instance); the raising property is skipped.
        state = default_save(Model())
        assert state == {"good": {"k": 1}}


class TestPeripheralRegistry:
    def test_round_trip_across_models(self):
        UARTPublisher.rx_buffers[0].extend("uart")
        GPIO.gpio_state["PA1"] = 1
        Interrupts.active[2] = True
        Interrupts.enabled[2] = True

        reg = PeripheralRegistry()
        snap = reg.snapshot()

        # Mutate every model.
        UARTPublisher.rx_buffers[0].clear()
        GPIO.gpio_state["PA1"] = 0
        Interrupts.active[2] = False

        assert reg.restore(snap) is True
        assert list(UARTPublisher.rx_buffers[0]) == list("uart")
        assert GPIO.gpio_state["PA1"] == 1
        assert Interrupts.active[2] is True
        assert Interrupts.Active_Interrupts is Interrupts.active

    def test_registry_captures_hal_stats(self):
        from halucinator import hal_stats
        hal_stats.stats.clear()
        hal_stats.stats["boots"] = 1

        reg = PeripheralRegistry()
        snap = reg.snapshot()

        hal_stats.stats["boots"] = 42
        hal_stats.stats["extra"] = "x"

        assert reg.restore(snap) is True
        assert hal_stats.stats == {"boots": 1}

    def test_restore_false_when_target_missing(self):
        reg = PeripheralRegistry()
        snap = reg.snapshot()
        # A captured target that no longer resolves to a live object.
        snap["model:nonexistent.Model"] = {"x": 1}
        assert reg.restore(snap) is False

    def test_snapshot_raises_when_a_target_save_fails(self):
        """A registered handler whose save_state raises must make the whole
        snapshot raise (never a partial capture)."""
        from halucinator.bp_handlers import intercepts

        class BadSave:
            def save_state(self):
                raise ValueError("cannot capture me")

        intercepts.initalized_classes["bad_save"] = BadSave()
        try:
            with pytest.raises(RuntimeError, match="snapshot failed"):
                PeripheralRegistry().snapshot()
        finally:
            intercepts.initalized_classes.pop("bad_save", None)

    def test_restore_false_when_a_target_restore_fails(self):
        """A live target whose restore_state returns False makes the registry
        restore return False (all-or-nothing)."""
        from halucinator.bp_handlers import intercepts

        class BadRestore:
            def save_state(self):
                return {"v": 1}

            def restore_state(self, _state):
                return False

        intercepts.initalized_classes["bad_restore"] = BadRestore()
        try:
            reg = PeripheralRegistry()
            snap = reg.snapshot()
            assert reg.restore(snap) is False
        finally:
            intercepts.initalized_classes.pop("bad_restore", None)

    def test_timer_model_snapshots_parameters_not_threads(self):
        """TimerModel.active_timers holds live Event/Thread pairs — the
        generic deep-copy chokes on those (the full-suite order-dependency
        bug this guards against). Its explicit save/restore captures timer
        PARAMETERS and relaunches threads on restore."""
        from halucinator.peripheral_models.timer_model import TimerModel
        try:
            # Rate is huge so no tick ever fires during the test.
            TimerModel.start_timer("snap_test_timer", 42, 3600.0)

            reg = PeripheralRegistry()
            snap = reg.snapshot()  # must not raise on the live thread
            key = ("model:halucinator.peripheral_models.timer_model."
                   "TimerModel")
            assert snap[key]["timers"]["snap_test_timer"] == {
                "irq_num": 42, "rate": 3600.0, "delay": 0, "stopped": False}

            TimerModel.stop_timer("snap_test_timer")
            TimerModel.active_timers.clear()

            assert reg.restore(snap) is True
            assert "snap_test_timer" in TimerModel.active_timers
            _ev, thread = TimerModel.active_timers["snap_test_timer"]
            assert thread.irq_num == 42 and thread.rate == 3600.0
        finally:
            TimerModel.shutdown()
            TimerModel.active_timers.clear()
