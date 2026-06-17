# Copyright 2026 Christopher Wright

"""SessionManager — supervises one emulation worker subprocess per session.

The MCP server holds a single SessionManager (no HalucinatorSession of its
own). Each `start_emulation` spawns a `python -m halucinator.mcp._worker`
subprocess that owns exactly one HalucinatorSession, and every tool call is
proxied to the right worker over the line-delimited JSON-RPC pipe (see
_codec). This is what makes several firmware images drivable from one server
despite HALucinator's process-wide global state (intercept LUTs,
peripheral_server zmq sockets) — see _worker.py for the full rationale.

Each session gets a distinct rx/tx port pair so the per-worker
peripheral_server endpoints (whose ipc:// paths embed the port) never collide.
"""
from __future__ import annotations

import atexit
import collections
import logging
import os
import select
import subprocess
import sys
import threading
import time
from typing import Any, Deque, Dict, List, Optional, Tuple

from . import _codec
from .session import SessionError, DEFAULT_CONT_TIMEOUT

log = logging.getLogger(__name__)


# Read timeouts (seconds) for the manager-side wait on a worker response.
# These are backstops against a wedged worker — a crashed worker is detected
# immediately via EOF on the pipe regardless of timeout. `start` loads firmware
# and binds zmq, so it's allowed to be slow; `cont` gets timeout+grace computed
# per call.
_DEFAULT_READ_TIMEOUT = 60.0
_START_READ_TIMEOUT = 180.0
_CONT_GRACE = 10.0


def _worker_env() -> Dict[str, str]:
    """Environment for a worker subprocess.

    The worker must import halucinator, but it runs from the server's cwd
    (which a test may have chdir'd) so a relative PYTHONPATH=src wouldn't
    resolve. Anchor on the halucinator package's *actual* location — its
    source root is on the import path regardless of cwd, and works for both a
    pip/editable install and a PYTHONPATH-driven tree. Other deps live in
    site-packages, already on the child's default path."""
    import halucinator
    src_root = os.path.dirname(
        os.path.dirname(os.path.abspath(halucinator.__file__)))
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    parts = [src_root] + ([existing] if existing else [])
    env["PYTHONPATH"] = os.pathsep.join(parts)
    return env


class WorkerHandle:
    """Manager-side handle to one worker subprocess."""

    def __init__(self, session_id: str, proc: subprocess.Popen,
                 meta: Dict[str, Any], ports: Tuple[int, int]):
        self.session_id = session_id
        self.proc = proc
        self.meta = meta
        self.ports = ports
        self._lock = threading.Lock()
        self._rid = 0
        self._rbuf = b""  # leftover bytes after the last newline-framed read
        self._stderr_tail: Deque[str] = collections.deque(maxlen=200)
        # A worker logs heavily to stderr; drain it continuously so a full
        # pipe buffer can't backpressure and wedge the worker.
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr, daemon=True,
            name=f"worker-stderr-{session_id}")
        self._stderr_thread.start()

    def _drain_stderr(self) -> None:
        try:
            for line in self.proc.stderr:  # type: ignore[union-attr]
                self._stderr_tail.append(
                    line.decode("utf-8", "replace").rstrip())
        except Exception:  # noqa: BLE001 — pipe closed on teardown
            pass

    def _stderr_summary(self) -> str:
        return " | ".join(list(self._stderr_tail)[-5:]) or "(no stderr)"

    def call(self, method: str, params: Dict[str, Any],
             read_timeout: float = _DEFAULT_READ_TIMEOUT) -> Any:
        with self._lock:
            return self._rpc(method, params, read_timeout)

    def _rpc(self, method: str, params: Dict[str, Any],
             read_timeout: float) -> Any:
        """One request/response round-trip. Caller must hold self._lock."""
        if self.proc.poll() is not None:
            self.meta["state"] = "crashed"
            raise SessionError(
                f"session {self.session_id!r} is not running "
                f"(exited {self.proc.returncode}): {self._stderr_summary()}")
        req = _codec.encode_frame(
            _codec.make_request(self._next_id(), method, params))
        try:
            self.proc.stdin.write(req)        # type: ignore[union-attr]
            self.proc.stdin.flush()           # type: ignore[union-attr]
        except (BrokenPipeError, OSError) as exc:
            self.meta["state"] = "crashed"
            raise SessionError(
                f"session {self.session_id!r} crashed (write failed: "
                f"{exc}): {self._stderr_summary()}") from None

        line = self._read_frame(read_timeout)
        if line is None:
            # Worker is wedged (e.g. firmware unbreakable by emu_stop).
            self.meta["state"] = "wedged"
            self._force_kill()
            raise SessionError(
                f"session {self.session_id!r} timed out after "
                f"{read_timeout:.0f}s and was killed; start a fresh session")
        if line == b"":
            self.meta["state"] = "crashed"
            raise SessionError(
                f"session {self.session_id!r} crashed: "
                f"{self._stderr_summary()}")
        resp = _codec.decode_frame(line)
        if not resp.get("ok"):
            err = resp.get("error") or {}
            etype = err.get("type", "Error")
            msg = err.get("message", "unknown worker error")
            # A SessionError from the worker is a clean precondition failure —
            # surface its message verbatim. Anything else keeps its type
            # prefix so the cause isn't lost.
            if etype == "SessionError":
                raise SessionError(msg)
            raise SessionError(f"{etype}: {msg}")
        return resp.get("result")

    def _read_frame(self, timeout: float) -> Optional[bytes]:
        """Read one newline-terminated frame from the worker's stdout within
        *timeout* seconds total.

        Reads the raw fd directly (not the buffered Popen.stdout) so the
        select() timeout and the bytes consumed stay consistent — select on a
        buffered object can't see bytes already pulled into its buffer. Returns
        the frame (incl. trailing newline), b"" on EOF/crash, or None on
        timeout. Any bytes past the newline are retained for the next read.
        """
        fd = self.proc.stdout.fileno()          # type: ignore[union-attr]
        deadline = time.monotonic() + timeout
        while b"\n" not in self._rbuf:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            ready, _, _ = select.select([fd], [], [], remaining)
            if not ready:
                return None
            chunk = os.read(fd, 65536)
            if not chunk:
                return b""  # EOF — worker died
            self._rbuf += chunk
        line, _, rest = self._rbuf.partition(b"\n")
        self._rbuf = rest
        return line + b"\n"

    def _next_id(self) -> int:
        self._rid += 1
        return self._rid

    def _force_kill(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                try:
                    self.proc.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    pass

    def shutdown(self) -> None:
        """Best-effort graceful teardown, then ensure the process is gone.

        Acquire the per-worker lock non-blockingly: if a call is in flight
        (e.g. a slow `start`), don't wait on it — closing stdin + killing the
        process is enough, and waiting could stall atexit for the full read
        timeout. Only attempt the graceful `shutdown` RPC when no call holds
        the lock.
        """
        if self._lock.acquire(blocking=False):
            try:
                if self.proc.poll() is None:
                    self._rpc("shutdown", {}, read_timeout=10.0)
            except Exception:  # noqa: BLE001 — already crashing/wedged
                pass
            finally:
                self._lock.release()
        try:
            if self.proc.stdin is not None:
                self.proc.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            self._force_kill()


class SessionManager:
    """Owns the set of live worker sessions for one MCP server process."""

    def __init__(self, max_sessions: int = 4, port_base: int = 5600):
        self.max_sessions = max_sessions
        self.port_base = port_base
        self._sessions: Dict[str, WorkerHandle] = {}
        self._used_ports: set = set()
        self._counter = 0
        self._lock = threading.RLock()
        atexit.register(self.shutdown_all)

    # --- ports / ids -----------------------------------------------------

    def _alloc_ports(self) -> Tuple[int, int]:
        p = self.port_base
        while p in self._used_ports or (p + 1) in self._used_ports:
            p += 2
        self._used_ports.add(p)
        self._used_ports.add(p + 1)
        return p, p + 1

    def _free_ports(self, ports: Tuple[int, int]) -> None:
        self._used_ports.discard(ports[0])
        self._used_ports.discard(ports[1])

    # --- lifecycle -------------------------------------------------------

    def create(self, config_paths: List[str], emulator: str = "unicorn",
               target_name: str = "halucinator",
               session_id: Optional[str] = None,
               start_periph_server: bool = True) -> Dict[str, Any]:
        with self._lock:
            if len(self._sessions) >= self.max_sessions:
                raise SessionError(
                    f"max sessions ({self.max_sessions}) reached; "
                    f"shut one down before starting another")
            if session_id is None:
                self._counter += 1
                session_id = f"{target_name}-{self._counter}"
            if session_id in self._sessions:
                raise SessionError(f"session_id {session_id!r} already exists")
            rx, tx = self._alloc_ports()
            proc = subprocess.Popen(
                [sys.executable, "-m", "halucinator.mcp._worker"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, env=_worker_env(),
            )
            meta = {
                "session_id": session_id, "target_name": target_name,
                "emulator": emulator, "arch": None, "state": "starting",
                "rx_port": rx, "tx_port": tx,
            }
            handle = WorkerHandle(session_id, proc, meta, (rx, tx))
            self._sessions[session_id] = handle

        # Drive the start RPC outside the registry lock (the worker has its
        # own lock); on any failure, reap the half-born session without
        # masking the original start error (destroy() may itself raise if a
        # concurrent teardown already removed the session).
        try:
            result = handle.call("start", {
                "config_paths": config_paths, "emulator": emulator,
                "target_name": target_name, "rx_port": rx, "tx_port": tx,
                "start_periph_server": start_periph_server,
            }, read_timeout=_START_READ_TIMEOUT)
        except Exception:
            try:
                self.destroy(session_id)
            except Exception:  # noqa: BLE001
                pass
            raise
        handle.meta["arch"] = result.get("arch")
        handle.meta["state"] = "ready"
        result["session_id"] = session_id
        return result

    def resolve(self, session_id: Optional[str]) -> WorkerHandle:
        """Map a (possibly omitted) session_id to a worker. With exactly one
        session, session_id may be omitted and resolves to it."""
        with self._lock:
            if session_id is not None:
                handle = self._sessions.get(session_id)
                if handle is None:
                    raise SessionError(f"no such session: {session_id!r}")
                return handle
            if not self._sessions:
                raise SessionError(
                    "no active sessions; call start_emulation first")
            if len(self._sessions) == 1:
                return next(iter(self._sessions.values()))
            raise SessionError(
                f"multiple sessions active ({sorted(self._sessions)}); "
                f"pass session_id to pick one")

    def call(self, session_id: Optional[str], method: str,
             read_timeout: Optional[float] = None, **params: Any) -> Any:
        handle = self.resolve(session_id)
        if read_timeout is None:
            if method == "cont" and params.get("blocking", True):
                read_timeout = float(
                    params.get("timeout", DEFAULT_CONT_TIMEOUT)) + _CONT_GRACE
            else:
                read_timeout = _DEFAULT_READ_TIMEOUT
        return handle.call(method, params, read_timeout=read_timeout)

    def list_sessions(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [
                dict(h.meta, alive=(h.proc.poll() is None))
                for h in self._sessions.values()
            ]

    def destroy(self, session_id: str) -> Dict[str, Any]:
        with self._lock:
            handle = self._sessions.pop(session_id, None)
            if handle is None:
                raise SessionError(f"no such session: {session_id!r}")
        # Tear the worker down BEFORE freeing its ports: the worker's
        # peripheral_server holds zmq ipc:// endpoints derived from those
        # ports until the process actually exits, so releasing them earlier
        # would let a new session grab them and collide.
        handle.shutdown()
        with self._lock:
            self._free_ports(handle.ports)
        return {"session_id": session_id, "shutdown": True}

    def shutdown_all(self) -> None:
        with self._lock:
            handles = list(self._sessions.values())
            self._sessions.clear()
            self._used_ports.clear()
        for handle in handles:
            try:
                handle.shutdown()
            except Exception:  # noqa: BLE001
                log.exception("error shutting down session %s",
                              handle.session_id)
