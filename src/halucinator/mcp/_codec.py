# Copyright 2026 Christopher Wright

"""Wire codec shared by the MCP server, the SessionManager, and its worker
subprocesses.

Two concerns live here so there's a single source of truth:

1. Hex (de)serialisation of raw memory. Bytes can't cross a JSON boundary —
   neither the MCP tool boundary (read_memory returns a hex string to the
   client) nor the manager<->worker pipe — so memory always travels as a
   lowercase hex string.
2. The line-delimited JSON-RPC framing used between manager.py and
   _worker.py: one JSON object per line on stdin/stdout.

The BYTES_* tables let the worker transparently convert the one session
method that returns bytes (read_memory) and the one that takes bytes
(write_memory) without special-casing them at every call site.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional


# --- hex <-> bytes -------------------------------------------------------

def bytes_to_hex(data: bytes) -> str:
    return data.hex()


def hex_to_bytes(s: str) -> bytes:
    s = s.strip().lower()
    if s.startswith("0x"):
        s = s[2:]
    if len(s) % 2 != 0:
        raise ValueError("hex string must have even length")
    return bytes.fromhex(s)


# --- per-method bytes handling for the manager<->worker boundary ---------

# Session methods whose return value is raw bytes; hex-encode it over the wire.
BYTES_RESULT_METHODS = frozenset({"read_memory"})
# Session methods with a single bytes-typed parameter -> that parameter's name;
# the worker hex-decodes it before the call.
BYTES_PARAM_METHODS: Dict[str, str] = {"write_memory": "data"}


# --- JSON-RPC framing ----------------------------------------------------

def encode_frame(obj: Dict[str, Any]) -> bytes:
    """Serialise one protocol object to a single newline-terminated line."""
    return (json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8")


def decode_frame(line: bytes) -> Dict[str, Any]:
    """Parse one protocol line back into an object."""
    return json.loads(line.decode("utf-8"))


def make_request(rid: int, method: str, params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return {"id": rid, "method": method, "params": params or {}}


def make_ok(rid: Optional[int], result: Any) -> Dict[str, Any]:
    return {"id": rid, "ok": True, "result": result}


def make_error(
    rid: Optional[int], exc_type: str, message: str, tb: str = "",
) -> Dict[str, Any]:
    return {
        "id": rid,
        "ok": False,
        "error": {"type": exc_type, "message": message, "traceback": tb},
    }
