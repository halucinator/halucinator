# Copyright 2026 Christopher Wright

"""SocketBridge — host TCP server <-> firmware BSD-socket syscalls (no TAP).

Bridges a real host TCP client (pymodbus, a PLC configurator, or a stdlib socket) to a
rehosted firmware's OWN server socket by intercepting the firmware's VxWorks
socket *thunks* (select / accept / recv / send / close / shutdown / setsockopt
/ __errno) at the socket-syscall layer. The firmware's real server loop runs;
we just feed its select/accept/recv and ship its send bytes to the host client.

Designed for an ARM/VxWorks PLC Port502Server (Modbus/UMAS :502), whose
PollMsgToRout() is a NON-BLOCKING select()-driven poll over the listen fd plus
all connection fds. Because the firmware polls, the bridge never blocks the
emulator thread: each select() hook reports readiness from host-side state that
a background TCP thread maintains; accept/recv/send mutate guest memory only on
the emulator thread when the thunk fires.

THREADING (load-bearing):
  - ONE host TCP server daemon thread (accept loop) + per-connection reader and
    writer daemon threads. These touch ONLY plain-Python state under self._lock
    (deques/bytearrays/dicts/sockets). They NEVER call any qemu.* / unicorn API
    (unicorn is not thread-safe).
  - The EMU thread runs the firmware and, on each intercepted thunk, calls the
    matching hook which does ALL guest memory/register access and returns the
    syscall result. No hook blocks (no sleeps / no socket I/O on the emu thread).

SCOPING (safety): the thunks are shared by every socket in the firmware, so each
hook only bridges Port502Server traffic and PASSES THROUGH everything else:
  - select  -> bridged only when the caller (lr) is inside PollMsgToRout.
  - accept  -> bridged only when listenfd == the configured listen_fd.
  - recv/send/close/shutdown/setsockopt -> bridged only when fd is a bridge fd
    (in self._fd2conn) or the listen_fd; otherwise the real syscall runs.
  - __errno -> returns our scratch errno (EWOULDBLOCK-ish) only on the single
    call immediately following a bridge recv that reported "no data"; otherwise
    the real __errno runs.

YAML (one SocketBridge instance owns all entries; cached by class):
  - class: halucinator.bp_handlers.SocketBridge
    function: select        # role; addr = the select thunk
    addr: 0x20236f90
    registration_args: { tcp_port: 502, listen_fd: 11, first_conn_fd: 200,
                         max_conns: 32, bind_host: "0.0.0.0",
                         poll_caller_lo: 0x200c91d0, poll_caller_hi: 0x200c962f,
                         errno_scratch_addr: 0x04700000, errno_value: 0x46,
                         verbose: false }
  - { class: ..SocketBridge, function: accept,     addr: 0x201faebc }
  - { class: ..SocketBridge, function: recv,       addr: 0x201fb354 }
  - { class: ..SocketBridge, function: send,       addr: 0x201fb16c }
  - { class: ..SocketBridge, function: close,      addr: 0x20231244 }
  - { class: ..SocketBridge, function: shutdown,   addr: 0x201fb6a8 }
  - { class: ..SocketBridge, function: setsockopt, addr: 0x201fb46c }
  - { class: ..SocketBridge, function: __errno,    addr: 0x2022941c }
"""
from __future__ import annotations

import collections
import socket
import struct
import threading
from typing import TYPE_CHECKING, Any, Deque, Dict, List, Optional, cast

from halucinator.bp_handlers.bp_handler import BPHandler, HandlerFunction, HandlerReturn, bp_handler
from halucinator import hal_log

if TYPE_CHECKING:
    from halucinator.backends.hal_backend import HalBackend

hlog = hal_log.getHalLogger()

U32 = 0xFFFFFFFF
NEG1 = 0xFFFFFFFF   # -1 as an ARM int return


class _HostConn:
    """A host TCP client connection bridged to a guest fd."""
    __slots__ = ("sock", "peer", "rx", "tx", "guest_fd", "state", "tx_event")

    def __init__(self, sock: socket.socket, peer: Any) -> None:
        self.sock = sock
        self.peer = peer                 # (ip, port)
        self.rx = bytearray()            # host -> firmware
        self.tx = bytearray()            # firmware -> host
        self.guest_fd: int = -1
        self.state = "pending"           # pending -> open -> peer_closed -> dead
        self.tx_event = threading.Event()


class SocketBridge(BPHandler):
    """Socket-syscall-layer host<->firmware TCP bridge. See module docstring."""

    def __init__(self) -> None:
        self.func_names: Dict[int, str] = {}     # addr -> role
        self._lock = threading.Lock()
        self._pending: Deque[_HostConn] = collections.deque()   # accepted, no fd yet
        self._fd2conn: Dict[int, _HostConn] = {}
        self._free_fds: List[int] = []
        self._next_fd: int = 0
        self._errno_pending = False              # set by recv "no data"; consumed by __errno
        self._server_started = False
        # global config (set on first register_handler)
        self.tcp_port = 502
        self.listen_fd = 11
        self.first_conn_fd = 200
        self.max_conns = 32
        self.bind_host = "0.0.0.0"
        self.poll_lo = 0x200c91d0
        self.poll_hi = 0x200c962f
        self.errno_scratch = 0x04700000
        self.errno_value = 0x46
        self.verbose = False

    # ---- registration -------------------------------------------------
    def register_handler(  # pylint: disable=too-many-arguments
        self, qemu: "HalBackend", addr: int, func_name: str,
        tcp_port: int = 502, listen_fd: int = 11, first_conn_fd: int = 200,
        max_conns: int = 32, bind_host: str = "0.0.0.0",
        poll_caller_lo: int = 0x200c91d0, poll_caller_hi: int = 0x200c962f,
        errno_scratch_addr: int = 0x04700000, errno_value: int = 0x46,
        verbose: bool = False,
    ) -> HandlerFunction:  # pylint: disable=unused-argument
        self.func_names[addr] = func_name
        if not self._server_started:
            self.tcp_port = int(tcp_port)
            self.listen_fd = int(listen_fd)
            self.first_conn_fd = int(first_conn_fd)
            self._next_fd = int(first_conn_fd)
            self.max_conns = int(max_conns)
            self.bind_host = bind_host
            self.poll_lo = int(poll_caller_lo)
            self.poll_hi = int(poll_caller_hi)
            self.errno_scratch = int(errno_scratch_addr)
            self.errno_value = int(errno_value)
            self.verbose = bool(verbose)
            self._server_started = True
            t = threading.Thread(target=self._server_thread, name="SocketBridge-502",
                                 daemon=True)
            t.start()
            hlog.info("SocketBridge: host TCP server starting on %s:%d "
                      "(listen_fd=%d, conn_fds=%d..%d)", self.bind_host, self.tcp_port,
                      self.listen_fd, self.first_conn_fd, self.first_conn_fd + self.max_conns - 1)
        return cast(HandlerFunction, SocketBridge.on_socket_call)

    # ---- host side (background threads; NO qemu access) ---------------
    def _server_thread(self) -> None:
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind((self.bind_host, self.tcp_port))
            srv.listen(8)
        except Exception as e:  # noqa: BLE001
            hlog.error("SocketBridge: cannot bind %s:%d (%s) -- bridge inactive",
                       self.bind_host, self.tcp_port, e)
            return
        hlog.info("SocketBridge: listening on tcp/%d", self.tcp_port)
        while True:
            try:
                cs, peer = srv.accept()
            except Exception:  # noqa: BLE001
                break
            cs.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            conn = _HostConn(cs, peer)
            with self._lock:
                if len(self._fd2conn) + len(self._pending) >= self.max_conns:
                    hlog.warning("SocketBridge: max_conns reached, refusing %s", peer)
                    try:
                        cs.close()
                    except Exception:  # noqa: BLE001
                        pass
                    continue
                self._pending.append(conn)
            hlog.info("SocketBridge: host client connected %s (pending accept)", peer)
            threading.Thread(target=self._reader_thread, args=(conn,), daemon=True).start()
            threading.Thread(target=self._writer_thread, args=(conn,), daemon=True).start()

    def _reader_thread(self, conn: _HostConn) -> None:
        sock = conn.sock
        while True:
            try:
                data = sock.recv(4096)
            except Exception:  # noqa: BLE001
                data = b""
            if not data:
                with self._lock:
                    if conn.state in ("pending", "open"):
                        conn.state = "peer_closed"
                conn.tx_event.set()
                return
            with self._lock:
                conn.rx.extend(data)

    def _writer_thread(self, conn: _HostConn) -> None:
        sock = conn.sock
        while True:
            conn.tx_event.wait()
            conn.tx_event.clear()
            with self._lock:
                if conn.tx:
                    chunk = bytes(conn.tx)
                    del conn.tx[:]
                else:
                    chunk = b""
                dead = conn.state == "dead"
            if chunk:
                try:
                    sock.sendall(chunk)
                except Exception:  # noqa: BLE001
                    with self._lock:
                        conn.state = "peer_closed"
            if dead:
                try:
                    sock.close()
                except Exception:  # noqa: BLE001
                    pass
                return

    # ---- emu side (single dispatch thread; all qemu access here) ------
    @bp_handler
    def on_socket_call(self, qemu: "HalBackend", addr: int) -> HandlerReturn:
        role = self.func_names.get(addr, "")
        if role == "select":
            return self._h_select(qemu)
        if role == "accept":
            return self._h_accept(qemu)
        if role == "recv":
            return self._h_recv(qemu)
        if role == "send":
            return self._h_send(qemu)
        if role == "close":
            return self._h_close(qemu)
        if role == "shutdown":
            return self._h_shutdown(qemu)
        if role == "setsockopt":
            return self._h_setsockopt(qemu)
        if role == "__errno":
            return self._h_errno(qemu)
        return False, None

    def _h_select(self, qemu: "HalBackend") -> HandlerReturn:
        # Only bridge PollMsgToRout's select; pass others through to the real one.
        lr = qemu.get_ret_addr()
        if not (self.poll_lo <= lr <= self.poll_hi):
            return False, None
        nfds = qemu.get_arg(0)
        readfds = qemu.get_arg(1)
        if readfds == 0:
            return True, 0
        nwords = ((nfds + 31) >> 5)
        if nwords < 1:
            nwords = 1
        if nwords > 64:           # the master set is 0x100 bytes = 64 words
            nwords = 64
        ready_fds: List[int] = []
        with self._lock:
            if self._pending:
                ready_fds.append(self.listen_fd)
            for fd, conn in self._fd2conn.items():
                if conn.rx or conn.state == "peer_closed":
                    ready_fds.append(fd)
        # zero the readfds region, then set only ready bits
        words = [0] * nwords
        count = 0
        for fd in ready_fds:
            w = fd >> 5
            if 0 <= w < nwords:
                words[w] |= 1 << (fd & 0x1F)
                count += 1
        for i in range(nwords):
            qemu.write_memory(readfds + i * 4, 4, words[i])
        if self.verbose and count:
            hlog.info("SocketBridge.select: ready=%s -> %d", ready_fds, count)
        return True, count

    def _h_accept(self, qemu: "HalBackend") -> HandlerReturn:
        listenfd = qemu.get_arg(0)
        if listenfd != self.listen_fd:
            return False, None      # not Port502Server's listen socket
        sa_ptr = qemu.get_arg(1)
        len_ptr = qemu.get_arg(2)
        with self._lock:
            if not self._pending:
                # no pending connection (select gates this; guard anyway)
                self._errno_pending = True
                return True, NEG1
            conn = self._pending.popleft()
            fd = self._free_fds.pop() if self._free_fds else self._next_fd
            if fd == self._next_fd:
                self._next_fd += 1
            conn.guest_fd = fd
            conn.state = "open"
            self._fd2conn[fd] = conn
            peer = conn.peer
        # fill sockaddr_in (VxWorks: len(1), family(1), port(be16), addr(be32), 8 zero)
        if sa_ptr:
            try:
                ip4 = socket.inet_aton(peer[0])
            except Exception:  # noqa: BLE001
                ip4 = b"\x7f\x00\x00\x01"
            sa = struct.pack(">BBH4s8x", 16, socket.AF_INET, peer[1] & 0xFFFF, ip4)
            qemu.write_memory_bytes(sa_ptr, sa)
        if len_ptr:
            qemu.write_memory(len_ptr, 4, 16)
        hlog.info("SocketBridge.accept: %s -> guest fd %d", peer, fd)
        return True, fd

    def _h_recv(self, qemu: "HalBackend") -> HandlerReturn:
        fd = qemu.get_arg(0)
        conn = self._fd2conn.get(fd)
        if conn is None:
            return False, None      # not a bridge socket -> real recv
        buf = qemu.get_arg(1)
        length = qemu.get_arg(2)
        with self._lock:
            have = len(conn.rx)
            if have > 0:
                n = min(length, have)
                chunk = bytes(conn.rx[:n])
                del conn.rx[:n]
            elif conn.state == "peer_closed":
                n = 0
                chunk = b""
            else:
                n = -1
                chunk = b""
        if n > 0:
            qemu.write_memory_bytes(buf, chunk)
            if self.verbose:
                hlog.info("SocketBridge.recv: fd %d -> %d bytes", fd, n)
            return True, n
        if n == 0:
            hlog.info("SocketBridge.recv: fd %d peer closed", fd)
            return True, 0
        # no data: -1 with errno=EWOULDBLOCK so rcvSocket retries (keeps conn)
        qemu.write_memory(self.errno_scratch, 4, self.errno_value)
        self._errno_pending = True
        return True, NEG1

    def _h_send(self, qemu: "HalBackend") -> HandlerReturn:
        fd = qemu.get_arg(0)
        conn = self._fd2conn.get(fd)
        if conn is None:
            return False, None      # not a bridge socket -> real send
        buf = qemu.get_arg(1)
        length = qemu.get_arg(2)
        if length <= 0:
            return True, 0
        data = qemu.read_memory_bytes(buf, length)
        with self._lock:
            conn.tx.extend(data)
        conn.tx_event.set()
        if self.verbose:
            hlog.info("SocketBridge.send: fd %d <- %d bytes", fd, length)
        return True, length

    def _h_close(self, qemu: "HalBackend") -> HandlerReturn:
        fd = qemu.get_arg(0)
        conn = self._fd2conn.get(fd)
        if conn is None:
            return False, None      # real close for non-bridge fds
        with self._lock:
            self._fd2conn.pop(fd, None)
            if fd != self._next_fd - 1:
                self._free_fds.append(fd)
            else:
                self._next_fd -= 1
            conn.state = "dead"
        conn.tx_event.set()          # wake writer to flush+close
        hlog.info("SocketBridge.close: guest fd %d torn down", fd)
        return True, 0

    def _h_shutdown(self, qemu: "HalBackend") -> HandlerReturn:
        fd = qemu.get_arg(0)
        if fd not in self._fd2conn:
            return False, None
        return True, 0               # let sockClose's close() do the teardown

    def _h_setsockopt(self, qemu: "HalBackend") -> HandlerReturn:
        fd = qemu.get_arg(0)
        if fd != self.listen_fd and fd not in self._fd2conn:
            return False, None
        return True, 0

    def _h_errno(self, qemu: "HalBackend") -> HandlerReturn:
        # Return our scratch errno pointer ONLY for the call right after a bridge
        # recv "no data"; otherwise run the real __errno.
        if self._errno_pending:
            self._errno_pending = False
            return True, self.errno_scratch
        return False, None
