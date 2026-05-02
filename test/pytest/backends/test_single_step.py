"""Single-step coverage across qemu, renode, and unicorn backends.

The avatar2 backend single-steps natively through its QemuTarget; this
suite verifies that the three other backends also implement step()
correctly.
"""
from unittest import mock

import pytest


# ---------------------------------------------------------------------------
# _GDBClient.step — sends an 's' packet
# ---------------------------------------------------------------------------

class TestGDBStepPacket:
    def test_step_sends_s_packet(self):
        from halucinator.backends.qemu_backend import _GDBClient
        client = _GDBClient.__new__(_GDBClient)
        client.host = "localhost"
        client.port = 1234
        client.timeout = 5.0
        client.arch = "arm"
        client._lock = __import__("threading").Lock()
        client._sock = mock.MagicMock()

        client.step()

        sent = client._sock.sendall.call_args[0][0]
        # GDB RSP 's' packet — $s#cs
        assert sent.startswith(b"$s#")


# ---------------------------------------------------------------------------
# QEMUBackend.step / RenodeBackend.step delegation
# ---------------------------------------------------------------------------

class TestBackendStepDelegation:
    def test_qemu_backend_step_waits_for_stop(self):
        from halucinator.backends.qemu_backend import QEMUBackend
        b = QEMUBackend.__new__(QEMUBackend)
        b._gdb = mock.MagicMock()
        b._gdb.wait_for_stop.return_value = "T05swbreak:;"
        b.step()
        b._gdb.step.assert_called_once()
        b._gdb.wait_for_stop.assert_called_once_with(timeout=2.0)

    def test_renode_backend_step_delegates(self):
        from halucinator.backends.renode_backend import RenodeBackend
        b = RenodeBackend(arch="cortex-m3")
        b._gdb = mock.MagicMock()
        b._gdb.wait_for_stop.return_value = "T05"
        b.step()
        b._gdb.step.assert_called_once()


# ---------------------------------------------------------------------------
# UnicornBackend.step — real execution
# ---------------------------------------------------------------------------

try:
    import unicorn  # noqa: F401
    _HAVE_UNICORN = True
except ImportError:
    _HAVE_UNICORN = False


@pytest.mark.skipif(not _HAVE_UNICORN, reason="unicorn-engine not installed")
class TestUnicornStep:
    def test_step_advances_pc_by_one_instruction(self):
        import struct
        from halucinator.backends.hal_backend import MemoryRegion
        from halucinator.backends.unicorn_backend import UnicornBackend

        b = UnicornBackend(arch="cortex-m3")
        b.add_memory_region(MemoryRegion("flash", 0x08000000, 0x10000, "rwx"))
        b.add_memory_region(MemoryRegion("ram",   0x20000000, 0x10000, "rw"))
        b.init()

        # Three Thumb instructions, each 2 bytes:
        #   20 07    movs r0, #7
        #   21 08    movs r1, #8
        #   22 09    movs r2, #9
        code = struct.pack("<HHH", 0x2007, 0x2108, 0x2209)
        b.write_memory(0x08000000, 1, code, len(code), raw=True)
        b.write_register("pc", 0x08000000)
        b.write_register("sp", 0x20008000)

        b.step()
        assert b.read_register("r0") == 7
        # r1/r2 shouldn't have been touched yet
        assert b.read_register("r1") == 0
        assert b.read_register("r2") == 0

        b.step()
        assert b.read_register("r1") == 8
        assert b.read_register("r2") == 0

        b.step()
        assert b.read_register("r2") == 9
