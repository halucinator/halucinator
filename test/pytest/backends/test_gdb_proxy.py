"""Tests for the user-facing GDB proxy (qemu + renode backends)."""
import socket
import threading
import time
from unittest import mock

import pytest


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _FakeUpstream(threading.Thread):
    """A tiny TCP server that echoes received bytes back, to stand in
    for the emulator's internal GDB socket."""

    def __init__(self):
        super().__init__(daemon=True)
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.sock.listen(1)
        self.peer = None
        self.started = threading.Event()

    def run(self):
        self.started.set()
        try:
            self.peer, _ = self.sock.accept()
        except OSError:
            return
        try:
            while True:
                data = self.peer.recv(4096)
                if not data:
                    return
                self.peer.sendall(data)  # echo
        except OSError:
            return

    def stop(self):
        try:
            self.sock.close()
        except OSError:
            pass
        if self.peer:
            try:
                self.peer.close()
            except OSError:
                pass


class TestGdbProxy:
    def test_proxy_pipes_user_to_backend(self):
        """End-to-end: user client sends bytes, proxy forwards to
        upstream which echoes, user sees bytes back. pause_cb and
        resume_cb fire once each."""
        from halucinator.backends.gdb_proxy import GdbProxy

        upstream = _FakeUpstream()
        upstream.start()
        upstream.started.wait(2.0)

        # Connect a fake backend to the upstream so proxy can proxy.
        backend_sock = socket.socket()
        backend_sock.connect(("127.0.0.1", upstream.port))
        backend = mock.Mock()
        backend._gdb = mock.Mock(_sock=backend_sock)

        pause_hits = []
        resume_hits = []
        user_port = _free_port()
        proxy = GdbProxy(user_port, backend,
                         pause_cb=lambda: pause_hits.append(1),
                         resume_cb=lambda: resume_hits.append(1))
        proxy.start()
        # Give proxy a moment to bind
        time.sleep(0.3)

        client = socket.socket()
        client.connect(("127.0.0.1", user_port))
        client.sendall(b"$qSupported#ff")
        # Give the pipe a chance to round-trip
        time.sleep(0.3)
        echoed = client.recv(4096)
        assert echoed == b"$qSupported#ff"

        client.close()
        # Let the proxy notice disconnect
        time.sleep(0.5)
        proxy.stop()
        upstream.stop()

        assert len(pause_hits) == 1
        assert len(resume_hits) == 1

    def test_stop_kills_accept_loop(self):
        """Calling stop() while waiting in accept() closes the listening
        socket and the thread exits."""
        from halucinator.backends.gdb_proxy import GdbProxy

        backend = mock.Mock(_gdb=mock.Mock(_sock=None))
        proxy = GdbProxy(_free_port(), backend)
        proxy.start()
        time.sleep(0.2)
        assert proxy.is_alive()
        proxy.stop()
        proxy.join(timeout=2.0)
        assert not proxy.is_alive()
