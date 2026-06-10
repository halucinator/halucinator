"""Coverage for the buffered _GDBClient receive path and GDB unix transport.

The old client read one byte per recv() syscall; the buffered version frames
packets out of a persistent buffer. These tests pin the framing edge cases
the buffer must get right (split packets, multiple packets per chunk,
non-destructive mid-packet timeout) plus the AF_UNIX / -gdb wiring.
"""
from __future__ import annotations

import socket
from unittest import mock

import pytest

from halucinator.backends.qemu_backend import QEMUBackend, _GDBClient


def _mk(stream_chunks, ack_mode=False):
    """A _GDBClient whose socket.recv yields the given chunks in order,
    then raises socket.timeout (mimicking a quiet stub)."""
    c = _GDBClient.__new__(_GDBClient)
    c.unix_path = None
    c._rxbuf = b""
    c._ack_mode = ack_mode
    c._lock = __import__("threading").Lock()
    it = iter(stream_chunks)

    def recv(_n):
        try:
            return next(it)
        except StopIteration:
            raise socket.timeout()
    c._sock = mock.MagicMock()
    c._sock.recv.side_effect = recv
    return c


class TestBufferedFraming:
    def test_packet_split_across_chunks(self):
        # "$OK#9a" arrives in three pieces.
        c = _mk([b"$O", b"K#", b"9a"])
        assert c._recv_pkt() == b"OK"

    def test_leading_acks_and_junk_skipped(self):
        c = _mk([b"+++$", b"OK#9a"])
        assert c._recv_pkt() == b"OK"

    def test_two_packets_in_one_chunk(self):
        # First call returns the first packet; second is served from the
        # buffer with NO further recv().
        c = _mk([b"$AA#00$BB#00"])
        assert c._recv_pkt() == b"AA"
        assert c._recv_pkt() == b"BB"
        # exactly one recv() happened (the rest came from the buffer)
        assert c._sock.recv.call_count == 1

    def test_checksum_bytes_consumed(self):
        # The 2 checksum chars after '#' must be dropped, not leak into the
        # next packet.
        c = _mk([b"$1234#ab$ZZ#cd"])
        assert c._recv_pkt() == b"1234"
        assert c._recv_pkt() == b"ZZ"

    def test_timeout_midpacket_is_nondestructive(self):
        # Partial packet, then timeout, then the rest. The first call raises
        # timeout; the buffer keeps the partial bytes so the retry completes.
        c = _mk([b"$OK"])  # no '#...' yet -> _fill_buf then StopIteration->timeout
        with pytest.raises(socket.timeout):
            c._recv_pkt()
        # Feed the remainder and retry — must frame correctly, not corrupt.
        rest = iter([b"#9a"])

        def recv2(_n):
            try:
                return next(rest)
            except StopIteration:
                raise socket.timeout()
        c._sock.recv.side_effect = recv2
        assert c._recv_pkt() == b"OK"

    def test_ack_sent_when_ack_mode(self):
        c = _mk([b"$OK#9a"], ack_mode=True)
        assert c._recv_pkt() == b"OK"
        c._sock.sendall.assert_any_call(b"+")


class TestGdbUnixTransport:
    def test_connect_unix_uses_af_unix(self):
        c = _GDBClient(unix_path="/tmp/hal-gdb.sock")
        created = {}

        def fake_socket(family, type_):
            created["family"] = family
            s = mock.MagicMock()
            # QStartNoAckMode handshake: '+' drain (timeout), then reply.
            s.recv.side_effect = [socket.timeout(), b"$OK#9a", socket.timeout()]
            return s

        with mock.patch("socket.socket", side_effect=fake_socket), \
                mock.patch.object(_GDBClient, "_discover_register_map",
                                  lambda self: None):
            c.connect()
        assert created["family"] == socket.AF_UNIX
        c._sock.connect.assert_called_once_with("/tmp/hal-gdb.sock")

    def test_backend_passes_gdb_unix_socket(self):
        b = QEMUBackend(arch="arm", gdb_unix_socket="/tmp/hal-gdb.sock")
        assert b.gdb_unix_socket == "/tmp/hal-gdb.sock"
        assert b._gdb.unix_path == "/tmp/hal-gdb.sock"

    def test_launch_emits_gdb_unix_arg(self):
        b = QEMUBackend(arch="arm", qemu_path="/bin/true",
                        gdb_unix_socket="/tmp/hal-gdb.sock")
        captured = {}
        with mock.patch("subprocess.Popen",
                        side_effect=lambda cmd, **k: captured.setdefault("cmd", cmd) or mock.MagicMock()), \
                mock.patch("time.sleep"), \
                mock.patch.object(b._gdb, "connect"), \
                mock.patch.object(b._qmp, "connect"):
            b.launch()
        assert "-gdb unix:/tmp/hal-gdb.sock,server,nowait" in captured["cmd"]


class TestLaunchUnixDefault:
    def _launch(self, **kw):
        b = QEMUBackend(arch="arm", qemu_path="/bin/true", **kw)
        captured = {}
        with mock.patch("subprocess.Popen",
                        side_effect=lambda cmd, **k: captured.setdefault("cmd", cmd) or mock.MagicMock()), \
                mock.patch("time.sleep"), \
                mock.patch("os.unlink"), \
                mock.patch.object(b._gdb, "connect"), \
                mock.patch.object(b._qmp, "connect"):
            b.launch()
        return b, captured["cmd"]

    def test_self_spawn_defaults_to_unix(self, monkeypatch):
        monkeypatch.delenv("HALUCINATOR_QEMU_TCP", raising=False)
        b, cmd = self._launch()
        assert "-gdb unix:" in cmd and "-qmp unix:" in cmd
        assert "-gdb tcp:" not in cmd and "-qmp tcp:" not in cmd
        # the auto-generated paths are now wired into the clients
        assert b._gdb.unix_path and b._gdb.unix_path in cmd
        assert b._qmp.unix_path and b._qmp.unix_path in cmd

    def test_env_opt_out_forces_tcp(self, monkeypatch):
        monkeypatch.setenv("HALUCINATOR_QEMU_TCP", "1")
        b, cmd = self._launch()
        assert "-gdb tcp::" in cmd and "-qmp tcp:" in cmd
        assert "unix:" not in cmd
        assert b._gdb.unix_path is None and b._qmp.unix_path is None
