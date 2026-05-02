"""
MMIO forwarding for RenodeBackend.

Renode's `Python.PythonPeripheral` runs a Python script inside the
Renode process on every bus access to the peripheral's range. That
script can't import halucinator's Python modules (different process,
different interpreter), so we bridge it over a TCP socket:

1. halucinator opens a TCP server on a free port.
2. For each AvatarPeripheral-subclass bp handler, we emit an entry in
   the generated .repl that points at a generated bridge script.
3. The bridge script opens a TCP connection to halucinator on IsInit,
   and on each read/write request sends a one-line RPC
   (`R <addr> <size>` / `W <addr> <size> <value>`). Halucinator's
   server dispatches to the peripheral and returns the result.
"""
from __future__ import annotations

import logging
import os
import socket
import threading
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)


_BRIDGE_TEMPLATE = '''# Auto-generated Halucinator MMIO bridge for Renode's Python.PythonPeripheral.
# Each request runs with module-level state (socket persists across calls).
import socket

_HAL_HOST = "127.0.0.1"
_HAL_PORT = {port}
_BASE = {base}

if request.IsInit:
    _sock = socket.socket()
    _sock.connect((_HAL_HOST, _HAL_PORT))
    _sock.sendall(("HELLO %x\\n" % _BASE).encode())
else:
    offset = request.Offset
    width = request.Width
    if request.IsRead:
        _sock.sendall(("R %x %d\\n" % (_BASE + offset, width)).encode())
        resp = _sock.recv(64).decode().strip()
        request.Value = int(resp, 0) if resp else 0
    else:
        _sock.sendall(("W %x %d %d\\n" % (_BASE + offset, width, request.Value)).encode())
        _sock.recv(16)
'''


class RenodeMMIOServer:
    """TCP server that services R/W RPC from Renode's PythonPeripheral
    bridge scripts.

    Each client connection represents one peripheral. The first line
    from the client is `HELLO <base_addr>`; we look up the peripheral
    registered at that address and route subsequent requests to it.
    """

    def __init__(self):
        self._sock: Optional[socket.socket] = None
        self.port: int = 0
        self._peripherals: Dict[int, Any] = {}
        self._stop = threading.Event()
        self._accept_thread: Optional[threading.Thread] = None
        self._client_threads: List[threading.Thread] = []

    def register(self, base_addr: int, peripheral: Any) -> None:
        self._peripherals[base_addr] = peripheral

    def start(self) -> int:
        self._sock = socket.socket()
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self.port = self._sock.getsockname()[1]
        self._sock.settimeout(0.5)
        self._accept_thread = threading.Thread(
            target=self._accept_loop, daemon=True, name="renode-mmio-accept"
        )
        self._accept_thread.start()
        log.info("Renode MMIO server listening on :%d", self.port)
        return self.port

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                client, addr = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            t = threading.Thread(
                target=self._serve, args=(client,), daemon=True,
                name=f"renode-mmio-client",
            )
            t.start()
            self._client_threads.append(t)

    def _serve(self, client: socket.socket) -> None:
        client.settimeout(5.0)
        try:
            # First line is the HELLO announcing which peripheral this is.
            line = self._readline(client)
            if not line.startswith("HELLO"):
                return
            try:
                base = int(line.split()[1], 16)
            except (IndexError, ValueError):
                return
            peripheral = self._peripherals.get(base)
            if peripheral is None:
                log.warning("Renode MMIO: no peripheral registered at 0x%x", base)
                return
            while not self._stop.is_set():
                try:
                    line = self._readline(client)
                except (ConnectionResetError, OSError):
                    return
                if not line:
                    return
                self._handle(line, peripheral, client)
        finally:
            try:
                client.close()
            except OSError:
                pass

    def _readline(self, sock: socket.socket) -> str:
        buf = b""
        while not self._stop.is_set():
            chunk = sock.recv(1)
            if not chunk:
                return ""
            if chunk == b"\n":
                return buf.decode("ascii", errors="replace")
            buf += chunk
        return ""

    def _handle(self, line: str, peripheral: Any, client: socket.socket) -> None:
        parts = line.split()
        if not parts:
            return
        op = parts[0]
        try:
            if op == "R":
                addr = int(parts[1], 16)
                size = int(parts[2])
                val = peripheral.read_memory(addr, size, num_words=1, raw=False)
                if isinstance(val, (bytes, bytearray)):
                    val = int.from_bytes(val[:size], "little")
                client.sendall(f"{int(val) & ((1 << (size * 8)) - 1):#x}\n"
                               .encode())
            elif op == "W":
                addr = int(parts[1], 16)
                size = int(parts[2])
                value = int(parts[3])
                peripheral.write_memory(addr, size, value)
                client.sendall(b"OK\n")
            else:
                log.warning("Renode MMIO: unknown op %r", op)
        except Exception:
            log.exception("Renode MMIO handler error")
            try:
                client.sendall(b"0\n")
            except OSError:
                pass

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass


def write_bridge_script(script_dir: str, base: int, port: int) -> str:
    """Render the per-peripheral bridge script and return its path."""
    path = os.path.join(script_dir, f"halmmio_{base:x}.py")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(_BRIDGE_TEMPLATE.format(port=port, base=base))
    return path


def emit_repl_python_peripherals(peripherals: List[Tuple[str, int, int]],
                                  script_dir: str, port: int) -> List[str]:
    """Return .repl lines for the given peripherals. Each entry is
    (name, base_addr, size)."""
    out: List[str] = []
    for name, base, size in peripherals:
        script_path = write_bridge_script(script_dir, base, port)
        out.append(f"{name}: Python.PythonPeripheral @ sysbus {hex(base)}")
        out.append(f"    size: {hex(size)}")
        out.append("    initable: true")
        out.append(f"    filename: \"{os.path.abspath(script_path)}\"")
        out.append("")
    return out
