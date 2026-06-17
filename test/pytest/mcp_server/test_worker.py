# Copyright 2026 Christopher Wright

"""Tests for the per-session worker subprocess (_worker.py).

Spawns ``python -m halucinator.mcp._worker`` and drives it over JSON-RPC,
exactly as the SessionManager will. The worker needs the halucinator runtime
(unicorn/avatar2 imports) but NOT the MCP SDK, so this runs on every
interpreter — it's the manager<->worker contract under test, in isolation.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC = str(REPO_ROOT / "src")
ARM32_DIR = REPO_ROOT / "test" / "multi_arch" / "arm32"
_FW_PRESENT = (ARM32_DIR / "firmware" / "test_uart.bin").exists()

pytestmark = pytest.mark.skipif(
    not _FW_PRESENT, reason="arm32 test firmware not built")


def _configs():
    return ["test_uart_config.yaml", "test_uart_addrs.yaml",
            "test_uart_memory.yaml"]


class _Worker:
    """Minimal manager-side stand-in: line-delimited JSON-RPC over pipes."""

    def __init__(self, cwd, capture_stdout_extra=False):
        # The worker runs from a different cwd, so hand it an ABSOLUTE import
        # path (mirrors what the SessionManager does): prepend src/ so the
        # child finds halucinator even when it isn't pip-installed (the 3.9
        # baseline drives the tree via PYTHONPATH=src, which is relative).
        env = dict(os.environ)
        env["PYTHONPATH"] = _SRC + os.pathsep + env.get("PYTHONPATH", "")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "halucinator.mcp._worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, cwd=str(cwd), env=env,
        )
        self._id = 0

    def call(self, method, **params):
        self._id += 1
        req = {"id": self._id, "method": method, "params": params}
        self.proc.stdin.write((json.dumps(req) + "\n").encode())
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        assert line, "worker closed the pipe without responding"
        # Every stdout line MUST be a valid JSON frame (stdout discipline).
        resp = json.loads(line)
        assert resp["id"] == self._id
        return resp

    def close(self):
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=15)
        except Exception:
            self.proc.kill()


def test_worker_roundtrip_and_bytes_hex():
    w = _Worker(ARM32_DIR)
    try:
        r = w.call("start", config_paths=_configs(), emulator="unicorn",
                   target_name="wtest", start_periph_server=False)
        assert r["ok"] is True, r
        assert r["result"]["arch"] == "cortex-m3"

        r = w.call("read_register", name="pc")
        assert r["ok"] and (r["result"] & ~1) == 0x08000112

        # Raw memory crosses the wire as hex in both directions, byte-for-byte
        # (no endianness — that's write_word's job).
        r = w.call("write_memory", addr=0x20001000, data="11223344")
        assert r["ok"] and r["result"] is True
        r = w.call("read_memory", addr=0x20001000, size=4)
        assert r["ok"] and r["result"] == "11223344"

        # write_word DOES apply target endianness (little on cortex-m3).
        r = w.call("write_word", addr=0x20001000, value=0x11223344)
        assert r["ok"] and r["result"] is True
        r = w.call("read_memory", addr=0x20001000, size=4)
        assert r["ok"] and r["result"] == "44332211"
        r = w.call("read_word", addr=0x20001000)
        assert r["ok"] and r["result"] == 0x11223344

        r = w.call("shutdown")
        assert r["ok"]
    finally:
        w.close()


def test_worker_large_frame_roundtrip():
    # A 64 KiB read_memory result is ~128 KiB of hex — larger than the OS pipe
    # buffer. Exercises the worker's buffered-writer (no short-write drop) and
    # the manager-side framing loop (accumulate across multiple reads).
    w = _Worker(ARM32_DIR)
    try:
        r = w.call("start", config_paths=_configs(), emulator="unicorn",
                   target_name="wbig", start_periph_server=False)
        assert r["ok"] is True, r
        payload = bytes(range(256)) * 256  # 65536 bytes, into RAM
        r = w.call("write_memory", addr=0x20002000, data=payload.hex())
        assert r["ok"] and r["result"] is True
        r = w.call("read_memory", addr=0x20002000, size=len(payload))
        assert r["ok"], r
        assert bytes.fromhex(r["result"]) == payload   # nothing dropped
        w.call("shutdown")
    finally:
        w.close()


def test_worker_error_propagation():
    w = _Worker(ARM32_DIR)
    try:
        # Querying before start -> SessionError surfaces with type + message.
        r = w.call("read_register", name="pc")
        assert r["ok"] is False
        assert r["error"]["type"] == "SessionError"
        assert "No active emulation" in r["error"]["message"]

        # Unknown/disallowed methods are rejected, not executed.
        r = w.call("__class__")
        assert r["ok"] is False and "disallowed" in r["error"]["message"]
        r = w.call("definitely_not_a_method")
        assert r["ok"] is False
    finally:
        w.close()


def test_worker_stdout_clean_with_peripheral_server():
    # With the peripheral server running (it logs heavily and prints), every
    # stdout line must still parse as a JSON frame — the worker redirects all
    # non-frame output to stderr. _Worker.call asserts each line is JSON.
    w = _Worker(ARM32_DIR)
    try:
        r = w.call("start", config_paths=_configs(), emulator="unicorn",
                   target_name="wtest_periph", start_periph_server=True,
                   rx_port=16555, tx_port=16556)
        assert r["ok"] is True, r
        r = w.call("status")
        assert r["ok"] and r["result"]["active"] is True
        r = w.call("shutdown")
        assert r["ok"]
    finally:
        w.close()
