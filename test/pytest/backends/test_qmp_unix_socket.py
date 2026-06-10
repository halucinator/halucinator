"""Coverage for the QMP Unix-domain-socket fast path (faster inject_irq
round-trips than loopback TCP) and the buffered line reader.
"""
from __future__ import annotations

import socket
from unittest import mock

from halucinator.backends.qemu_backend import QEMUBackend, _QMPClient


class TestQMPClientTransport:
    def test_unix_path_connects_af_unix(self):
        c = _QMPClient(unix_path="/tmp/hal-test-qmp.sock")
        created = {}

        def fake_socket(family, type_):
            created["family"] = family
            s = mock.MagicMock()
            # greeting line, then qmp_capabilities OK
            s.recv.side_effect = [b'{"QMP":{}}\n', b'{"return":{}}\n']
            return s

        with mock.patch("socket.socket", side_effect=fake_socket):
            c.connect()
        assert created["family"] == socket.AF_UNIX
        c._sock.connect.assert_called_once_with("/tmp/hal-test-qmp.sock")

    def test_tcp_path_connects_af_inet_with_nodelay(self):
        c = _QMPClient(host="localhost", port=4444)
        created = {}

        def fake_socket(family, type_):
            created["family"] = family
            s = mock.MagicMock()
            s.recv.side_effect = [b'{"QMP":{}}\n', b'{"return":{}}\n']
            return s

        with mock.patch("socket.socket", side_effect=fake_socket):
            c.connect()
        assert created["family"] == socket.AF_INET
        c._sock.connect.assert_called_once_with(("localhost", 4444))
        c._sock.setsockopt.assert_any_call(
            socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def test_recv_line_buffers_chunks_and_splits(self):
        c = _QMPClient(unix_path="/x")
        c._sock = mock.MagicMock()
        # Two JSON lines arrive split across recv() boundaries.
        c._sock.recv.side_effect = [b'{"a":', b'1}\n{"b":2}\n', b""]
        assert c._recv_line() == {"a": 1}
        # Second line is already buffered — no further recv needed.
        assert c._recv_line() == {"b": 2}


class TestBackendWiring:
    def test_backend_passes_unix_socket_to_client(self):
        b = QEMUBackend(arch="arm", qmp_unix_socket="/tmp/hal-x-qmp.sock")
        assert b.qmp_unix_socket == "/tmp/hal-x-qmp.sock"
        assert b._qmp.unix_path == "/tmp/hal-x-qmp.sock"

    def test_backend_defaults_to_tcp_when_no_socket(self):
        b = QEMUBackend(arch="arm")
        assert b.qmp_unix_socket is None
        assert b._qmp.unix_path is None

    def test_launch_emits_unix_qmp_arg(self):
        b = QEMUBackend(arch="arm", qemu_path="/bin/true",
                        qmp_unix_socket="/tmp/hal-x-qmp.sock")
        captured = {}

        def fake_popen(cmd, **kw):
            captured["cmd"] = cmd
            return mock.MagicMock()

        with mock.patch("subprocess.Popen", side_effect=fake_popen), \
                mock.patch("time.sleep"), \
                mock.patch.object(b._gdb, "connect"), \
                mock.patch.object(b._qmp, "connect"):
            b.launch()
        assert "-qmp unix:/tmp/hal-x-qmp.sock,server,nowait" in captured["cmd"]
        assert "tcp:" not in captured["cmd"].split("-qmp")[1]
