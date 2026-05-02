"""
Minimal TCP proxy that exposes a backend's internal GDB stub to an
external GDB/LLDB on a user-specified port.

The proxy listens on `user_port`, and when a client (user GDB) connects,
it pauses halucinator's dispatch loop, hands the underlying emulator's
GDB connection over to the user, and forwards bytes both ways until the
user disconnects. Then halucinator resumes its own dispatch.

This is deliberately a hand-off, not a multiplexer — the GDB Remote
Serial Protocol has no message ID scheme, so two simultaneous clients
would desync immediately. The hand-off model matches how avatar2's
`spawn_gdb_server` wraps the QEMU stub in practice.
"""
from __future__ import annotations

import logging
import select
import socket
import threading
from typing import Any, Optional

log = logging.getLogger(__name__)


class GdbProxy(threading.Thread):
    """Accept one user GDB connection at a time and forward bytes to
    the backend's internal GDB socket.

    Parameters
    ----------
    user_port : int
        TCP port the user points their GDB client at.
    backend : HalBackend
        The backend whose `_gdb._sock` we'll proxy.
    pause_cb : callable, optional
        Invoked (no args) right before we start forwarding. Backend
        uses this to pause its dispatch loop.
    resume_cb : callable, optional
        Invoked (no args) after the user disconnects.
    """

    def __init__(self, user_port: int, backend: Any,
                 pause_cb: Optional[Any] = None,
                 resume_cb: Optional[Any] = None):
        super().__init__(daemon=True, name=f"gdb-proxy:{user_port}")
        self.user_port = user_port
        self.backend = backend
        self.pause_cb = pause_cb
        self.resume_cb = resume_cb
        self._stop_evt = threading.Event()
        self._listen_sock: Optional[socket.socket] = None

    def run(self) -> None:
        self._listen_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._listen_sock.bind(("0.0.0.0", self.user_port))
            self._listen_sock.listen(1)
        except OSError as exc:
            log.error("GdbProxy bind on :%d failed: %s", self.user_port, exc)
            return
        self._listen_sock.settimeout(0.5)
        log.info("GDB proxy listening on :%d (forwards to backend GDB stub)",
                 self.user_port)

        while not self._stop_evt.is_set():
            try:
                client, addr = self._listen_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            log.info("GDB proxy: user connected from %s", addr)
            if self.pause_cb is not None:
                try:
                    self.pause_cb()
                except Exception:  # noqa: BLE001
                    log.exception("pause_cb failed")
            try:
                self._pump(client)
            finally:
                try:
                    client.close()
                except OSError:
                    pass
                if self.resume_cb is not None:
                    try:
                        self.resume_cb()
                    except Exception:  # noqa: BLE001
                        log.exception("resume_cb failed")
                log.info("GDB proxy: user disconnected")

        try:
            self._listen_sock.close()
        except OSError:
            pass

    def _pump(self, client: socket.socket) -> None:
        """Copy bytes between user's client and backend's GDB socket
        until either side closes."""
        upstream = self.backend._gdb._sock  # pylint: disable=protected-access
        if upstream is None:
            log.error("GdbProxy: backend GDB socket not open")
            return
        client.setblocking(False)
        upstream.setblocking(False)
        try:
            while not self._stop_evt.is_set():
                r, _, _ = select.select([client, upstream], [], [], 0.5)
                if client in r:
                    data = client.recv(4096)
                    if not data:
                        return
                    upstream.sendall(data)
                if upstream in r:
                    data = upstream.recv(4096)
                    if not data:
                        return
                    client.sendall(data)
        except (ConnectionResetError, BrokenPipeError, OSError) as exc:
            log.info("GdbProxy: connection closed (%s)", exc)
        finally:
            # Restore blocking mode on the upstream socket so halucinator's
            # dispatch loop can read from it again.
            try:
                upstream.setblocking(True)
            except OSError:
                pass

    def stop(self) -> None:
        self._stop_evt.set()
        if self._listen_sock:
            try:
                self._listen_sock.close()
            except OSError:
                pass
