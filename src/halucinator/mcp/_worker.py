# Copyright 2026 Christopher Wright

"""Per-session emulation worker — one process owns exactly one
HalucinatorSession and speaks line-delimited JSON-RPC to the SessionManager
over stdin/stdout.

Run as: ``python -m halucinator.mcp._worker``. Not a user-facing entry point;
manager.py spawns it.

Why a separate process per session: HALucinator keeps process-wide singleton
state (the bp_handlers intercept LUTs, the peripheral_server zmq sockets bound
to fixed ipc:// paths, class-level peripheral buffers), so two sessions cannot
safely share one interpreter. Isolating each session in its own process is the
only way to drive several firmware images at once without rewriting that core
state — and it also buys crash isolation (a unicorn segfault on bad firmware
takes down only its worker) and a hard-kill escape hatch for wedged firmware.

CRITICAL — stdout discipline: stdout carries ONLY JSON-RPC frames. Python
logging is sent to stderr, but some halucinator code prints to stdout directly
(e.g. peripheral_server.run_server's ``print``). To keep those from corrupting
the frame stream we dup the real stdout fd for framing at startup and then
point fd 1 (and sys.stdout) at stderr, so any stray writes land on stderr.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import traceback
from typing import Any, BinaryIO, Dict


log = logging.getLogger("halucinator.mcp.worker")


def _redirect_stdout_to_stderr() -> BinaryIO:
    """Reserve the real stdout for protocol frames; send everything else to
    stderr. Returns a *buffered* binary file writing to the original stdout
    (the pipe back to the manager).

    Buffered, not raw: a raw FileIO.write() does a single write() syscall and
    can short-write on a pipe, silently dropping the tail of a frame larger
    than the pipe buffer (a 64 KiB read_memory result is ~128 KiB of hex). A
    BufferedWriter loops until every byte is flushed, so callers must flush()
    after each frame.
    """
    real_stdout_fd = os.dup(sys.stdout.fileno())
    frame_out = os.fdopen(real_stdout_fd, "wb")
    # Point fd 1 at stderr so bare print()/C-level writes can't corrupt frames.
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    try:
        sys.stdout.flush()
    except Exception:  # noqa: BLE001
        pass
    return frame_out


def _allowed(session: Any, method: str) -> bool:
    """Only public methods defined on the HalucinatorSession class may be
    dispatched — never dunders, private helpers, or arbitrary attributes."""
    if not method or method.startswith("_"):
        return False
    fn = getattr(type(session), method, None)
    return callable(fn)


def _dispatch(session: Any, rid: Any, method: str,
              params: Dict[str, Any]) -> Dict[str, Any]:
    from . import _codec
    from .session import SessionError

    if not _allowed(session, method):
        return _codec.make_error(
            rid, "SessionError", f"unknown or disallowed method: {method!r}",
        )
    try:
        call_params = dict(params)
        bytes_param = _codec.BYTES_PARAM_METHODS.get(method)
        if bytes_param is not None and bytes_param in call_params:
            call_params[bytes_param] = _codec.hex_to_bytes(call_params[bytes_param])
        result = getattr(session, method)(**call_params)
        if method in _codec.BYTES_RESULT_METHODS:
            result = _codec.bytes_to_hex(result)
        return _codec.make_ok(rid, result)
    except SessionError as exc:
        return _codec.make_error(rid, "SessionError", str(exc),
                                 traceback.format_exc())
    except Exception as exc:  # noqa: BLE001
        return _codec.make_error(rid, type(exc).__name__, str(exc),
                                 traceback.format_exc())


def main() -> int:
    frame_out = _redirect_stdout_to_stderr()

    logging.basicConfig(
        level=logging.INFO, stream=sys.stderr,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    )

    from . import _codec
    from .session import HalucinatorSession

    session = HalucinatorSession()
    stdin = sys.stdin.buffer

    def emit(obj: Dict[str, Any]) -> None:
        # Buffered writer: write + flush guarantees the whole frame reaches
        # the manager even when it exceeds the pipe buffer.
        frame_out.write(_codec.encode_frame(obj))
        frame_out.flush()

    try:
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = _codec.decode_frame(line)
            except Exception as exc:  # noqa: BLE001 — malformed frame
                emit(_codec.make_error(None, "ProtocolError",
                                       f"bad frame: {exc}"))
                continue
            emit(_dispatch(session, req.get("id"), req.get("method", ""),
                           req.get("params") or {}))
    finally:
        # Manager closed the pipe (or we're exiting): tear the session down so
        # the backend, zmq sockets, and global LUTs are released cleanly.
        try:
            session.shutdown()
        except Exception:  # noqa: BLE001
            log.exception("worker: error during final shutdown")
    return 0


if __name__ == "__main__":
    sys.exit(main())
