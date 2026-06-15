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


class TestWaitForStopDrain:
    """The post-stop drain blocks for stop_drain_timeout seconds. QEMU sends
    exactly one stop reply, so its default must be 0.0 (non-blocking) — a
    fixed per-stop drain window was the bulk of the qemu-vs-avatar2 gap.
    Renode emits duplicate stop replies and opts back into a real window."""

    def test_default_drain_is_zero(self):
        c = _GDBClient(host="localhost", port=1234)
        assert c.stop_drain_timeout == 0.0

    def test_drain_does_not_block_when_zero(self):
        # One stop reply, then nothing. wait_for_stop must return immediately
        # without ever arming a positive (blocking) drain timeout.
        c = _mk([b"$T05#b9"])
        c.stop_drain_timeout = 0.0
        c._sock.gettimeout.return_value = None
        timeouts = []
        c._sock.settimeout = lambda t: timeouts.append(t)
        assert c.wait_for_stop(timeout=5.0) == "T05"
        # the drain phase must use a non-blocking (0.0) timeout, never 0.25
        assert 0.0 in timeouts
        assert all((t is None) or (t <= 0.0) or (t == 5.0) for t in timeouts)

    def test_renode_backend_opts_into_drain_window(self):
        from halucinator.backends.renode_backend import RenodeBackend
        b = RenodeBackend(arch="arm")
        assert b._gdb.stop_drain_timeout == 0.25


def _client_with_layout():
    """A _GDBClient with a known ARM-style register layout and no real socket,
    for exercising the per-stop register cache logic."""
    import threading
    c = _GDBClient.__new__(_GDBClient)
    c.unix_path = None
    c._rxbuf = b""
    c._ack_mode = False
    c._lock = threading.Lock()
    c._g_cache = None
    layout = {f"r{i}": (i * 4, 4) for i in range(13)}
    layout.update({"sp": (52, 4), "lr": (56, 4), "pc": (60, 4)})
    c._reg_layout = layout
    c._g_packet_size = 64
    return c


class TestRegisterCacheCoherence:
    """opt #1: a successful write must patch the per-stop register cache in
    place so a following read of any register stays a cache hit (no extra
    full-'g' round-trip)."""

    def test_write_register_patches_cache_in_place(self):
        c = _client_with_layout()
        c._g_cache = bytes(64)                 # warm, all-zero
        c._cmd = mock.MagicMock(return_value=b"OK")
        c.write_register("r0", 0x11223344)
        # P write issued; cache kept (not invalidated)
        assert c._cmd.call_args.args[0].startswith(b"P")
        assert c._g_cache is not None
        # the next read is served from the patched cache — no extra _cmd
        c._cmd.reset_mock()
        assert c.read_register("r0") == 0x11223344
        c._cmd.assert_not_called()

    def test_write_register_cold_cache_stays_cold(self):
        c = _client_with_layout()
        c._g_cache = None                      # cold
        c._cmd = mock.MagicMock(return_value=b"OK")
        c.write_register("pc", 0x08001000)
        assert c._g_cache is None              # nothing to patch -> still cold


class TestBatchedRegisterWrite:
    """opt #2: write_registers collapses to a single 'G' round-trip when a warm
    cache is available; falls back to individual 'P' writes when cold."""

    def test_warm_cache_single_G_roundtrip(self):
        c = _client_with_layout()
        c._g_cache = bytes(64)                  # warm
        c._cmd = mock.MagicMock(return_value=b"OK")
        c.write_registers({"r0": 0xAABBCCDD, "pc": 0x08001000})
        # exactly one round-trip, and it's a 'G' (full register file) write
        assert c._cmd.call_count == 1
        assert c._cmd.call_args.args[0].startswith(b"G")
        # cache updated -> both reads are hits
        c._cmd.reset_mock()
        assert c.read_register("r0") == 0xAABBCCDD
        assert c.read_register("pc") == 0x08001000
        c._cmd.assert_not_called()

    def test_cold_cache_falls_back_to_P_writes(self):
        c = _client_with_layout()
        c._g_cache = None                       # cold
        c._cmd = mock.MagicMock(return_value=b"OK")
        c.write_registers({"r0": 1, "pc": 2})
        # two individual writes, each a 'P'
        assert c._cmd.call_count == 2
        assert all(call.args[0].startswith(b"P")
                   for call in c._cmd.call_args_list)


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
