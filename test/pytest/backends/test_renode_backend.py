"""Unit tests for RenodeBackend — sockets and subprocess fully mocked."""
from unittest import mock

import pytest

from halucinator.backends.renode_backend import (
    RenodeBackend, _MonitorClient, _ARCH_MAP,
)


# ---------------------------------------------------------------------------
# _MonitorClient
# ---------------------------------------------------------------------------

class TestMonitorClient:
    def test_drain_returns_available_bytes(self):
        import socket as _socket
        client = _MonitorClient()
        client._sock = mock.Mock()
        client._sock.gettimeout.return_value = 5.0
        # recv returns one chunk, then raises timeout so _drain exits.
        client._sock.recv.side_effect = [b"hello world", _socket.timeout()]
        out = client._drain(0.1)
        assert b"hello" in out

    def test_execute_sends_crlf(self):
        import socket as _socket
        client = _MonitorClient()
        client._sock = mock.Mock()
        client._sock.gettimeout.return_value = 5.0
        client._sock.recv.side_effect = [b"ok", _socket.timeout()]
        client.execute("mach create foo")
        # Command should be sent with \r\n terminator
        sent = client._sock.sendall.call_args[0][0]
        assert sent.endswith(b"\r\n")
        assert b"mach create foo" in sent


# ---------------------------------------------------------------------------
# RenodeBackend (without running Renode)
# ---------------------------------------------------------------------------

@pytest.fixture
def backend():
    b = RenodeBackend(arch="cortex-m3")
    b._gdb = mock.MagicMock()
    b._monitor = mock.MagicMock()
    b._process = None
    return b


class TestRenodeBackend:
    def test_is_hal_backend(self):
        from halucinator.backends.hal_backend import HalBackend
        assert issubclass(RenodeBackend, HalBackend)

    def test_arch_map_covers_renode_supported_archs(self):
        # Renode 1.16+ ships CPU.MIPS in the linux-arm64-dotnet-portable
        # release, so mips is now a first-class arch in the map.
        assert "cortex-m3" in _ARCH_MAP
        assert "arm" in _ARCH_MAP
        assert "arm64" in _ARCH_MAP
        assert "mips" in _ARCH_MAP
        assert "powerpc" in _ARCH_MAP
        assert "ppc64" in _ARCH_MAP

    def test_read_register_delegates_to_gdb(self, backend):
        backend._gdb.read_register.return_value = 0xCAFE
        assert backend.read_register("r0") == 0xCAFE
        backend._gdb.read_register.assert_called_once_with("r0")

    def test_write_register_uses_monitor_for_cortex_m(self, backend):
        # backend fixture is cortex-m3; pc writes should hit Monitor not GDB.
        backend._monitor._sock = mock.Mock()  # make Monitor "connected"
        backend.write_register("pc", 0x8000)
        backend._monitor.execute.assert_called_once()
        cmd = backend._monitor.execute.call_args[0][0]
        assert cmd == "cpu PC 0x8000"
        backend._gdb.write_register.assert_not_called()

    def test_write_register_uses_property_syntax_for_sp_and_lr(self, backend):
        """SP / LR are CPU properties on Renode and accept the
        ``cpu <REG> <val>`` shorthand directly."""
        backend._monitor._sock = mock.Mock()
        backend.write_register("sp", 0x20001000)
        backend.write_register("lr", 0x10000123)
        assert backend._monitor.execute.call_args_list[0][0][0] == "cpu SP 0x20001000"
        assert backend._monitor.execute.call_args_list[1][0][0] == "cpu LR 0x10000123"

    def test_write_register_uses_setregister_for_r0_through_r12(self, backend):
        """R0-R12 are NOT property-accessible on Renode's CortexM CPU;
        writing ``cpu R0 <val>`` errors with "sysbus.cpu does not provide
        a field, method or property R0". They must use the ``SetRegister
        <index> <val>`` method instead. Without this, ReturnConstant
        ret_value silently delivers 0 to the firmware (e.g. the bpv5 demo
        fails with mcu_detect_revision returning 0 instead of 10)."""
        backend._monitor._sock = mock.Mock()
        for n in range(13):
            backend.write_register(f"r{n}", 0xA000 + n)
        calls = [c[0][0] for c in backend._monitor.execute.call_args_list]
        assert calls == [f"cpu SetRegister {n} {0xA000 + n:#x}" for n in range(13)]

    def test_write_register_uppercase_r_normalised_to_setregister(self, backend):
        """Register names are lowercased before dispatch so callers can
        pass either ``r0`` or ``R0``."""
        backend._monitor._sock = mock.Mock()
        backend.write_register("R3", 0xDEADBEEF)
        backend._monitor.execute.assert_called_once_with("cpu SetRegister 3 0xdeadbeef")

    def test_write_register_falls_back_to_gdb_on_monitor_exception(self, backend):
        """If the Monitor write raises, the backend logs a warning and
        falls through to the GDB packet, so a transient Monitor failure
        doesn't lose the write entirely."""
        backend._monitor._sock = mock.Mock()
        backend._monitor.execute.side_effect = RuntimeError("monitor closed")
        backend.write_register("r0", 0x42)
        backend._gdb.write_register.assert_called_once_with("r0", 0x42)

    def test_write_register_delegates_to_gdb_for_non_cortex_m(self):
        """For non-cortex-m archs, register writes still go through GDB."""
        b = RenodeBackend(arch="arm64")
        b._gdb = mock.MagicMock()
        b._monitor = mock.MagicMock()
        b.write_register("x0", 0x1234)
        b._gdb.write_register.assert_called_once_with("x0", 0x1234)

    def test_set_breakpoint_returns_id(self, backend):
        bp = backend.set_breakpoint(0x1000)
        assert isinstance(bp, int)
        backend._gdb.set_breakpoint.assert_called_once_with(0x1000)
        # Remove uses the stored addr
        backend.remove_breakpoint(bp)
        backend._gdb.remove_breakpoint.assert_called_once_with(0x1000)

    def test_inject_irq_uses_monitor(self, backend):
        backend.inject_irq(5)
        # IRQ delivery on cortex-m goes through the NVIC peripheral
        # and is asserted as a pulse (assert-then-deassert) so the
        # CPU sees an edge.
        assert backend._monitor.execute.call_count == 2
        for call in backend._monitor.execute.call_args_list:
            arg = call[0][0]
            assert "OnGPIO" in arg
            assert "5" in arg
            assert "sysbus.nvic" in arg or "sysbus.cpu" in arg
        assert "True" in backend._monitor.execute.call_args_list[0][0][0]
        assert "False" in backend._monitor.execute.call_args_list[1][0][0]

    def test_first_cont_sends_c_then_start(self, backend):
        """The first cont() queues GDB `c` and then un-pauses the machine
        via Monitor `start`. Either alone is insufficient: `c` on a
        paused machine is a no-op, `start` without a pending `c` makes
        Renode emit a spurious initial-halt stop reply."""
        backend._machine_started = False
        backend.cont()
        backend._gdb.cont.assert_called_once()
        backend._monitor.execute.assert_called_with("start")
        assert backend._machine_started is True

    def test_subsequent_cont_uses_gdb(self, backend):
        backend._machine_started = True  # previous cont already ran
        backend.cont()
        backend._gdb.cont.assert_called_once()
        backend._monitor.execute.assert_not_called()

    def test_unknown_arch_rejected_by_resc(self):
        b = RenodeBackend(arch="cortex-m3")  # construct OK
        b.arch = "martian"  # then break
        b.add_memory_region(mock.Mock(name="m", base_addr=0, size=0x1000,
                                       file=None))
        with pytest.raises(ValueError, match="Unsupported arch"):
            b._write_resc_script("/tmp/does-not-need-to-exist.resc")

    def test_resc_script_references_firmware_file(self, tmp_path):
        from halucinator.backends.hal_backend import MemoryRegion
        b = RenodeBackend(arch="cortex-m3")
        firmware = tmp_path / "fw.bin"
        firmware.write_bytes(b"\x00\x00\x00\x00")
        b.add_memory_region(MemoryRegion(
            name="flash", base_addr=0x8000000, size=0x1000,
            file=str(firmware), permissions="rx",
        ))
        script = tmp_path / "halucinator.resc"
        b._write_resc_script(str(script))
        text = script.read_text()
        assert "mach create" in text
        assert str(firmware) in text
        assert "0x8000000" in text
