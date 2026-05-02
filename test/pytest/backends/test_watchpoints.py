"""Watchpoint support across QEMU, Renode, and Unicorn backends."""
from unittest import mock

import pytest


# ---------------------------------------------------------------------------
# _GDBClient Z2/Z3/Z4 packet shape
# ---------------------------------------------------------------------------

class TestGDBWatchpointPackets:
    def _client_with_reply(self, reply):
        from halucinator.backends.qemu_backend import _GDBClient
        client = _GDBClient.__new__(_GDBClient)
        client.host = "localhost"
        client.port = 1234
        client.timeout = 5.0
        client.arch = "arm"
        client._lock = __import__("threading").Lock()
        client._sock = mock.MagicMock()
        # _cmd sends and reads one packet; fake it entirely.
        client._cmd = mock.Mock(return_value=reply)
        return client

    def test_write_watch_sends_Z2(self):
        c = self._client_with_reply(b"OK")
        c.set_watchpoint(0x20000100, size=4, read=False, write=True)
        c._cmd.assert_called_once_with(b"Z2,20000100,4")

    def test_read_watch_sends_Z3(self):
        c = self._client_with_reply(b"OK")
        c.set_watchpoint(0x20000200, size=2, read=True, write=False)
        c._cmd.assert_called_once_with(b"Z3,20000200,2")

    def test_access_watch_sends_Z4(self):
        c = self._client_with_reply(b"OK")
        c.set_watchpoint(0x20000300, size=1, read=True, write=True)
        c._cmd.assert_called_once_with(b"Z4,20000300,1")

    def test_remove_write_watch_sends_z2(self):
        c = self._client_with_reply(b"OK")
        c.remove_watchpoint(0x20000100, size=4, read=False, write=True)
        c._cmd.assert_called_once_with(b"z2,20000100,4")

    def test_watch_without_read_or_write_rejected(self):
        c = self._client_with_reply(b"OK")
        with pytest.raises(ValueError):
            c.set_watchpoint(0x1000, size=4, read=False, write=False)


# ---------------------------------------------------------------------------
# QEMUBackend / RenodeBackend wrappers
# ---------------------------------------------------------------------------

@pytest.fixture
def qemu_backend():
    from halucinator.backends.qemu_backend import QEMUBackend
    b = QEMUBackend.__new__(QEMUBackend)
    b.arch = "cortex-m3"
    b.config = None
    b._bp_map = {}
    b._next_bp_id = 1
    b._regions = []
    b._process = None
    b._gdb = mock.MagicMock()
    b._qmp = mock.MagicMock()
    return b


@pytest.fixture
def renode_backend():
    from halucinator.backends.renode_backend import RenodeBackend
    b = RenodeBackend(arch="cortex-m3")
    b._gdb = mock.MagicMock()
    b._monitor = mock.MagicMock()
    return b


class TestQEMUBackendWatchpoints:
    def test_set_and_remove_write_watch(self, qemu_backend):
        bp = qemu_backend.set_watchpoint(0x40000000, write=True, read=False, size=4)
        assert isinstance(bp, int)
        qemu_backend._gdb.set_watchpoint.assert_called_once_with(
            0x40000000, size=4, read=False, write=True
        )
        qemu_backend.remove_watchpoint(bp)
        qemu_backend._gdb.remove_watchpoint.assert_called_once_with(
            0x40000000, size=4, read=False, write=True
        )

    def test_remove_invalid_id_noop(self, qemu_backend):
        qemu_backend.remove_watchpoint(9999)
        qemu_backend._gdb.remove_watchpoint.assert_not_called()


class TestRenodeBackendWatchpoints:
    def test_set_watch_delegates_to_gdb(self, renode_backend):
        bp = renode_backend.set_watchpoint(0x20001000, write=True, read=True)
        renode_backend._gdb.set_watchpoint.assert_called_once_with(
            0x20001000, size=4, read=True, write=True
        )
        renode_backend.remove_watchpoint(bp)
        renode_backend._gdb.remove_watchpoint.assert_called_once()


# ---------------------------------------------------------------------------
# UnicornBackend real watchpoint (in-process execution)
# ---------------------------------------------------------------------------

try:
    import unicorn  # noqa: F401
    _HAVE_UNICORN = True
except ImportError:
    _HAVE_UNICORN = False


@pytest.mark.skipif(not _HAVE_UNICORN, reason="unicorn-engine not installed")
class TestUnicornWatchpoint:
    def _make_backend(self):
        import struct
        from halucinator.backends.hal_backend import MemoryRegion
        from halucinator.backends.unicorn_backend import UnicornBackend
        b = UnicornBackend(arch="cortex-m3")
        b.add_memory_region(MemoryRegion("flash", 0x08000000, 0x10000, "rwx"))
        b.add_memory_region(MemoryRegion("ram", 0x20000000, 0x10000, "rw"))
        b.init()
        return b

    def test_write_watchpoint_halts_on_write(self):
        import struct
        b = self._make_backend()
        # Thumb: movs r0, #1; str r0, [r1]; nop
        # r1 needs to point to watched address; use immediate+orr? simpler:
        # Put target address in r1 via a literal pool; for brevity we just
        # set r1 via register write before emu_start.
        # Thumb insns:
        #   2001  movs r0, #1
        #   6008  str  r0, [r1]
        #   46c0  nop
        code = struct.pack("<HHH", 0x2001, 0x6008, 0x46c0)
        b.write_memory(0x08000000, 1, code, len(code), raw=True)
        b.write_register("pc", 0x08000000)
        b.write_register("r1", 0x20001000)

        wp_id = b.set_watchpoint(0x20001000, size=4, write=True)
        assert isinstance(wp_id, int)

        b.cont()
        # After the watch fires the str was either just executed or about
        # to execute — either way r0 should be 1 (from movs) and execution
        # should have stopped near the str.
        assert b.read_register("r0") == 1
        assert b._bp_hit_addr == 0x20001000

        # Remove and let it run to completion (nop loop).
        b.remove_watchpoint(wp_id)
