"""Coordinator tests: the composite SystemSnapshot bundling Layer-1 (backend)
and Layer-2 (peripherals), plus the RestoreResult failure contract.

The headline test boots a tiny Unicorn machine, fills peripheral state, takes a
system snapshot, corrupts BOTH layers, restores, and asserts the whole system —
guest memory + registers AND python peripheral state — is byte-identical.
"""
from collections import defaultdict, deque

import pytest

from halucinator.backends.hal_backend import MemoryRegion, Snapshot
from halucinator.peripheral_models.gpio import GPIO
from halucinator.peripheral_models.interrupts import Interrupts
from halucinator.peripheral_models.uart import UARTPublisher
from halucinator.snapshot import (
    PeripheralRegistry,
    RestoreResult,
    SystemSnapshot,
    system_restore,
    system_snapshot,
)

try:
    import unicorn  # noqa: F401
    _HAVE_UNICORN = True
except ImportError:
    _HAVE_UNICORN = False

FLASH_BASE = 0x08000000
RAM_BASE = 0x20000000


@pytest.fixture(autouse=True)
def clean_periph_state():
    UARTPublisher.rx_buffers = defaultdict(deque)
    GPIO.gpio_state = defaultdict(int)
    Interrupts.active = defaultdict(bool)
    Interrupts.Active_Interrupts = Interrupts.active
    Interrupts.enabled = defaultdict(bool)
    yield


def _make_unicorn():
    from halucinator.backends.unicorn_backend import UnicornBackend
    b = UnicornBackend(arch="cortex-m3")
    b.add_memory_region(MemoryRegion("flash", FLASH_BASE, 0x10000, "rwx"))
    b.add_memory_region(MemoryRegion("ram", RAM_BASE, 0x8000, "rw"))
    b.init()
    return b


def _full_backend_dump(b):
    mem = {base: bytes(b._uc.mem_read(base, end - base + 1))
           for (base, end, _p) in b._uc.mem_regions()}
    regs = {name: b.read_register(name) for name in b.list_registers()}
    return mem, regs


@pytest.mark.skipif(not _HAVE_UNICORN, reason="unicorn-engine not installed")
class TestSystemRoundTrip:
    def test_byte_identical_backend_and_peripherals(self):
        b = _make_unicorn()
        b.write_memory(RAM_BASE, 1, bytes(range(256)) * 2, 512, raw=True)
        b.write_register("r0", 0xABCDEF01)
        b.write_register("sp", RAM_BASE + 0x2000)

        UARTPublisher.rx_buffers[0].extend("greetings")
        GPIO.gpio_state["LED"] = 1
        Interrupts.active[7] = True
        Interrupts.enabled[7] = True

        reg = PeripheralRegistry()
        snap = system_snapshot(b, reg)
        assert isinstance(snap, SystemSnapshot)

        before_mem, before_regs = _full_backend_dump(b)

        # Corrupt BOTH layers.
        b.write_memory(RAM_BASE, 1, b"\x00" * 512, 512, raw=True)
        b.write_register("r0", 0)
        UARTPublisher.rx_buffers[0].clear()
        GPIO.gpio_state["LED"] = 0
        Interrupts.active[7] = False

        result = system_restore(b, snap, reg)
        assert result.ok is True
        assert result.layer is None

        after_mem, after_regs = _full_backend_dump(b)
        assert after_mem == before_mem
        assert after_regs == before_regs
        assert list(UARTPublisher.rx_buffers[0]) == list("greetings")
        assert GPIO.gpio_state["LED"] == 1
        assert Interrupts.active[7] is True

    def test_context_manager_releases(self):
        b = _make_unicorn()
        with system_snapshot(b) as snap:
            assert snap._released is False
        assert snap._released is True

    def test_release_is_idempotent(self):
        b = _make_unicorn()
        snap = system_snapshot(b)
        snap.release()
        assert snap._released is True
        snap.release()  # second call hits the early-return, must not raise
        assert snap._released is True

    def test_system_snapshot_raises_leaves_no_bundle(self):
        """If the peripheral layer fails to capture, the backend snapshot is
        released and the exception propagates — no half-bundle is returned."""
        b = _make_unicorn()

        class BoomRegistry:
            def snapshot(self):
                raise RuntimeError("peripheral capture boom")

        with pytest.raises(RuntimeError, match="boom"):
            system_snapshot(b, BoomRegistry())


class TestRestoreResultFailureContract:
    def test_backend_failure_reported(self):
        """When the backend refuses (incompatible snapshot), system_restore
        reports layer='backend' and never touches peripherals."""
        class FakeBackend:
            def restore_state(self, snap):
                return False

        snap = SystemSnapshot(
            backend=Snapshot(backend_type="X", version=1, data=None),
            peripherals={})
        result = system_restore(FakeBackend(), snap, PeripheralRegistry())
        assert isinstance(result, RestoreResult)
        assert result.ok is False
        assert result.layer == "backend"
        assert "backend" in result.message

    def test_peripheral_failure_reported(self):
        """Backend restores OK but a peripheral target is missing → the result
        names the peripherals layer."""
        class OkBackend:
            def restore_state(self, snap):
                return True

        reg = PeripheralRegistry()
        periph = reg.snapshot()
        periph["model:ghost.Model"] = {"x": 1}  # unresolvable on restore

        snap = SystemSnapshot(
            backend=Snapshot(backend_type="X", version=1, data=None),
            peripherals=periph)
        result = system_restore(OkBackend(), snap, reg)
        assert result.ok is False
        assert result.layer == "peripherals"

    def test_device_layer_failure_reported(self):
        """Backend + peripherals restore OK, but the device layer refuses →
        the result names the devices layer."""
        class OkBackend:
            def restore_state(self, snap):
                return True

        class FailingDeviceLayer:
            def restore(self, _states):
                return False

        snap = SystemSnapshot(
            backend=Snapshot(backend_type="X", version=1, data=None),
            peripherals={},
            devices={"bridge-1": {"count": 7}})
        result = system_restore(OkBackend(), snap, PeripheralRegistry(),
                                device_layer=FailingDeviceLayer())
        assert result.ok is False
        assert result.layer == "devices"
        assert "device layer restore failed" in result.message
