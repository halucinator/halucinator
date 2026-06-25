# Copyright 2026 Christopher Wright

"""Tests for the multi-session SessionManager (manager.py).

Drives the manager directly (it spawns real worker subprocesses but doesn't
need the MCP SDK), against the arm32 firmware. Covers: concurrent independent
sessions, reaping, session_id resolution, max-sessions, error propagation
through the pipe, and crash handling (a killed worker surfaces a clean error
rather than hanging).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from halucinator.mcp.manager import SessionManager
from halucinator.mcp.session import SessionError


REPO_ROOT = Path(__file__).resolve().parents[3]
ARM32_DIR = REPO_ROOT / "test" / "multi_arch" / "arm32"
_FW_PRESENT = (ARM32_DIR / "firmware" / "test_uart.bin").exists()

pytestmark = pytest.mark.skipif(
    not _FW_PRESENT, reason="arm32 test firmware not built")


def _configs():
    return ["test_uart_config.yaml", "test_uart_addrs.yaml",
            "test_uart_memory.yaml"]


@pytest.fixture
def manager(tmp_path, monkeypatch):
    # Workers inherit cwd; the YAML references firmware/ relatively.
    monkeypatch.chdir(ARM32_DIR)
    # High port base so per-session peripheral_server ports don't collide
    # with anything else on the box.
    mgr = SessionManager(max_sessions=2, port_base=17600)
    yield mgr
    mgr.shutdown_all()


def test_single_session_resolves_without_id(manager):
    info = manager.create(_configs(), emulator="unicorn", target_name="solo")
    assert info["arch"] == "cortex-m3"
    sid = info["session_id"]
    # session_id may be omitted with exactly one session.
    pc = manager.call(None, "read_register", name="pc")
    assert pc & ~1 == 0x08000112
    # explicit id works too
    assert manager.call(sid, "read_register", name="pc") == pc
    out = manager.destroy(sid)
    assert out["shutdown"] is True
    assert manager.list_sessions() == []


def test_two_sessions_independent_and_reaped(manager):
    a = manager.create(_configs(), target_name="alpha")["session_id"]
    b = manager.create(_configs(), target_name="beta")["session_id"]
    assert {s["session_id"] for s in manager.list_sessions()} == {a, b}

    # Distinct rx/tx ports per session (no peripheral_server collision).
    metas = {s["session_id"]: s for s in manager.list_sessions()}
    assert metas[a]["rx_port"] != metas[b]["rx_port"]

    # Independent memory at the same address.
    manager.call(a, "write_word", addr=0x20001000, value=0xAAAAAAAA)
    manager.call(b, "write_word", addr=0x20001000, value=0x55555555)
    assert manager.call(a, "read_word", addr=0x20001000) == 0xAAAAAAAA
    assert manager.call(b, "read_word", addr=0x20001000) == 0x55555555

    # Ambiguous resolution with >1 session and no id.
    with pytest.raises(SessionError, match="multiple sessions"):
        manager.call(None, "read_register", name="pc")

    procs = {a: manager.resolve(a).proc, b: manager.resolve(b).proc}
    manager.shutdown_all()
    assert manager.list_sessions() == []
    for p in procs.values():
        assert p.poll() is not None  # worker reaped


def test_max_sessions_enforced(manager):
    manager.create(_configs(), target_name="one")
    manager.create(_configs(), target_name="two")
    with pytest.raises(SessionError, match="max sessions"):
        manager.create(_configs(), target_name="three")


def test_bad_config_propagates_error_and_reaps(manager):
    with pytest.raises(SessionError, match="not found"):
        manager.create(["/no/such/config.yaml"], target_name="bad")
    # The half-born session must have been reaped (no leak, port freed).
    assert manager.list_sessions() == []


def test_unknown_session_id(manager):
    with pytest.raises(SessionError, match="no such session"):
        manager.call("ghost", "read_register", name="pc")


def test_crashed_worker_surfaces_clean_error(manager):
    sid = manager.create(_configs(), target_name="victim")["session_id"]
    handle = manager.resolve(sid)
    # Hard-kill the worker out from under the manager.
    handle.proc.kill()
    handle.proc.wait(timeout=5)
    # The next call must raise (not hang) and report the crash.
    with pytest.raises(SessionError, match="not running|crashed"):
        manager.call(sid, "read_register", name="pc")
