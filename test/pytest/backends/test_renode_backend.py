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
        # mips is intentionally missing — the Renode linux-arm64-dotnet-
        # portable release doesn't ship a MIPS CPU class.
        assert "cortex-m3" in _ARCH_MAP
        assert "arm64" in _ARCH_MAP
        assert "powerpc" in _ARCH_MAP
        assert "ppc64" in _ARCH_MAP
        assert "mips" not in _ARCH_MAP

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
        assert "PC" in cmd and "0x8000" in cmd
        backend._gdb.write_register.assert_not_called()

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
        backend._monitor.execute.assert_called_once()
        call_arg = backend._monitor.execute.call_args[0][0]
        assert "5" in call_arg
        assert "OnGPIO" in call_arg

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
