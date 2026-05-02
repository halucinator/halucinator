"""Unit tests for QEMUBackend — sockets are fully mocked."""
import struct
from unittest import mock

import pytest

from halucinator.backends.qemu_backend import QEMUBackend, _GDBClient, _QMPClient


# ---------------------------------------------------------------------------
# _GDBClient helpers
# ---------------------------------------------------------------------------

def _make_gdb_pkt(payload: bytes) -> bytes:
    cs = sum(payload) & 0xFF
    return b"$" + payload + b"#" + f"{cs:02x}".encode()


class TestGDBFraming:
    """RSP packet framing: checksum, send_pkt formatting, recv_pkt parsing."""

    def test_checksum_sum_mod_256(self):
        assert _GDBClient._checksum(b"") == 0
        assert _GDBClient._checksum(b"g") == ord("g")
        assert _GDBClient._checksum(b"QStartNoAckMode") == \
            sum(b"QStartNoAckMode") & 0xFF

    def test_send_pkt_formats_as_dollar_payload_hash_cs(self):
        """$payload#cc where cc is two lowercase hex chars of the checksum."""
        c = _GDBClient(port=0)
        c._sock = mock.MagicMock()
        c._send_pkt(b"g")
        sent = c._sock.sendall.call_args[0][0]
        assert sent.startswith(b"$g#")
        assert sent[-2:] == f"{ord('g'):02x}".encode()

    def test_recv_pkt_strips_framing_and_acks(self):
        """Stub emits '+' then $OK#9a; client returns 'OK' and sends '+' ack."""
        stream = iter([b"+", b"$", b"O", b"K", b"#", b"9", b"a"])
        sock = mock.MagicMock()
        sock.recv = lambda n: next(stream)
        c = _GDBClient(port=0)
        c._sock = sock
        c._ack_mode = True
        assert c._recv_pkt() == b"OK"
        sock.sendall.assert_called_once_with(b"+")

    def test_recv_pkt_no_ack_mode_does_not_send_plus(self):
        stream = iter([b"$", b"O", b"K", b"#", b"9", b"a"])
        sock = mock.MagicMock()
        sock.recv = lambda n: next(stream)
        c = _GDBClient(port=0)
        c._sock = sock
        c._ack_mode = False
        assert c._recv_pkt() == b"OK"
        sock.sendall.assert_not_called()


class TestGDBClient:
    def _client_with_sock(self, responses):
        """Create a _GDBClient whose socket returns *responses* in order."""
        client = _GDBClient.__new__(_GDBClient)
        client.host = "localhost"
        client.port = 1234
        client.timeout = 5.0
        client.arch = "arm"
        client._lock = __import__("threading").Lock()

        # Build a byte stream from the responses
        stream = b""
        for r in responses:
            stream += _make_gdb_pkt(r)

        idx = [0]

        def fake_recv(n):
            chunk = stream[idx[0]: idx[0] + n]
            idx[0] += len(chunk)
            return chunk

        sock = mock.MagicMock()
        sock.recv.side_effect = fake_recv
        sock.sendall = mock.MagicMock()
        client._sock = sock
        return client

    def test_read_register_r0(self):
        # 26 registers × 8 hex chars each = 208 chars
        # r0 = 0x12345678, rest = 0
        r0_le = (0x12345678).to_bytes(4, "little").hex().encode()
        rest = b"00000000" * 25
        client = self._client_with_sock([r0_le + rest])
        assert client.read_register("r0") == 0x12345678

    def test_read_register_pc(self):
        regs = [(0xDEADBEEF if i == 15 else 0).to_bytes(4, "little").hex()
                for i in range(26)]
        payload = "".join(regs).encode()
        client = self._client_with_sock([payload])
        assert client.read_register("pc") == 0xDEADBEEF

    def test_read_memory(self):
        client = self._client_with_sock([b"deadbeef"])
        data = client.read_memory(0x08000000, 4)
        assert data == bytes.fromhex("deadbeef")

    def test_write_memory(self):
        client = self._client_with_sock([b"OK"])
        client.write_memory(0x08000000, b"\x01\x02\x03\x04")
        call_bytes = client._sock.sendall.call_args[0][0]
        assert b"M8000000,4:01020304" in call_bytes

    def test_set_breakpoint(self):
        client = self._client_with_sock([b"OK"])
        client.set_breakpoint(0x08001000)
        call_bytes = client._sock.sendall.call_args[0][0]
        assert b"Z0,8001000" in call_bytes

    def test_remove_breakpoint(self):
        client = self._client_with_sock([b"OK"])
        client.remove_breakpoint(0x08001000)
        call_bytes = client._sock.sendall.call_args[0][0]
        assert b"z0,8001000" in call_bytes


# ---------------------------------------------------------------------------
# QEMUBackend
# ---------------------------------------------------------------------------

@pytest.fixture
def backend_with_mocks():
    """QEMUBackend with _GDBClient and _QMPClient fully mocked."""
    backend = QEMUBackend.__new__(QEMUBackend)
    backend.arch = "cortex-m3"
    backend.config = None
    backend._bp_map = {}
    backend._next_bp_id = 1
    backend._regions = []
    backend._process = None
    backend._gdb = mock.MagicMock(spec=_GDBClient)
    backend._qmp = mock.MagicMock(spec=_QMPClient)
    return backend


class TestQEMUBackend:
    def test_is_hal_backend(self):
        from halucinator.backends.hal_backend import HalBackend
        assert issubclass(QEMUBackend, HalBackend)

    def test_read_register(self, backend_with_mocks):
        b = backend_with_mocks
        b._gdb.read_register.return_value = 0xCAFE
        assert b.read_register("r0") == 0xCAFE
        b._gdb.read_register.assert_called_once_with("r0")

    def test_write_register(self, backend_with_mocks):
        b = backend_with_mocks
        b.write_register("pc", 0x8000)
        b._gdb.write_register.assert_called_once_with("pc", 0x8000)

    def test_read_memory_word(self, backend_with_mocks):
        b = backend_with_mocks
        b._gdb.read_memory.return_value = b"\x78\x56\x34\x12"
        val = b.read_memory(0x1000, 4, 1)
        assert val == 0x12345678

    def test_read_memory_raw(self, backend_with_mocks):
        b = backend_with_mocks
        b._gdb.read_memory.return_value = b"\x01\x02\x03\x04"
        data = b.read_memory(0x1000, 1, 4, raw=True)
        assert isinstance(data, bytes)
        assert data == b"\x01\x02\x03\x04"

    def test_write_memory_bytes(self, backend_with_mocks):
        b = backend_with_mocks
        b.write_memory(0x2000, 1, b"\xAB\xCD", 2, raw=True)
        b._gdb.write_memory.assert_called_once_with(0x2000, b"\xAB\xCD")

    def test_set_breakpoint_returns_id(self, backend_with_mocks):
        b = backend_with_mocks
        bp_id = b.set_breakpoint(0x8001000)
        assert isinstance(bp_id, int)
        b._gdb.set_breakpoint.assert_called_once_with(0x8001000)

    def test_remove_breakpoint(self, backend_with_mocks):
        b = backend_with_mocks
        bp_id = b.set_breakpoint(0x8001000)
        b.remove_breakpoint(bp_id)
        b._gdb.remove_breakpoint.assert_called_once_with(0x8001000)

    def test_remove_invalid_breakpoint_noop(self, backend_with_mocks):
        b = backend_with_mocks
        b.remove_breakpoint(9999)  # should not raise
        b._gdb.remove_breakpoint.assert_not_called()

    def test_cont(self, backend_with_mocks):
        b = backend_with_mocks
        b._gdb.wait_for_stop.return_value = "T05"
        b.cont()
        b._gdb.cont.assert_called_once()

    def test_stop(self, backend_with_mocks):
        b = backend_with_mocks
        b.stop()
        b._gdb.stop.assert_called_once()

    def test_inject_irq(self, backend_with_mocks):
        b = backend_with_mocks
        b.inject_irq(5)
        b._qmp.execute.assert_called_once_with(
            "avatar-armv7m-inject-irq",
            {"num_irq": 5, "num_cpu": 0},
        )

    def test_arm_mixin_get_arg_register(self, backend_with_mocks):
        """get_arg uses read_register via ARMHalMixin."""
        b = backend_with_mocks
        b._gdb.read_register.return_value = 42
        assert b.get_arg(0) == 42
        b._gdb.read_register.assert_called_with("r0")

    def test_arm_mixin_execute_return(self, backend_with_mocks):
        b = backend_with_mocks
        b._gdb.read_register.return_value = 0x08001234  # LR
        b._gdb.wait_for_stop.return_value = "T05"
        b.execute_return(0)
        b._gdb.write_register.assert_any_call("r0", 0)
        b._gdb.write_register.assert_any_call("pc", 0x08001234)
