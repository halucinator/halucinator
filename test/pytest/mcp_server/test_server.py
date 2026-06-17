# Copyright 2026 Christopher Wright

"""Tests for the FastMCP server layer (server.py).

These drive the registered tools — exercising the tool wiring, the hex
codec, and word endianness through the actual MCP request path (the
in-memory client/server session, which sets up the lifespan that holds the
HalucinatorSession). Requires the MCP SDK (Python 3.10+); conftest.py skips
this whole module when the SDK is absent.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

# The MCP SDK requires Python 3.10+; on the 3.9 baseline it's absent. Skip
# the whole module before the SDK imports below run.
pytest.importorskip("mcp", reason="MCP SDK requires Python 3.10+")

from halucinator.mcp.server import (
    build_server, bearer_auth_asgi, _bytes_to_hex, _hex_to_bytes,
)
from mcp.shared.memory import (
    create_connected_server_and_client_session as _connect,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
ARM32_DIR = REPO_ROOT / "test" / "multi_arch" / "arm32"
_FW_PRESENT = (ARM32_DIR / "firmware" / "test_uart.bin").exists()


def _arm32_configs():
    return [
        "test_uart_config.yaml",
        "test_uart_addrs.yaml",
        "test_uart_memory.yaml",
    ]


def _result(call_result):
    """Pull the typed return out of an mcp CallToolResult."""
    sc = call_result.structuredContent
    if sc and "result" in sc:
        return sc["result"]
    return [c.text for c in call_result.content]


# ---------------------------------------------------------------------------
# Hex codec (fast, no firmware) — previously untested
# ---------------------------------------------------------------------------

class TestHexCodec:
    @pytest.mark.parametrize("raw", [
        b"", b"\x00", b"halucinator", bytes(range(256)),
    ])
    def test_roundtrip(self, raw):
        assert _hex_to_bytes(_bytes_to_hex(raw)) == raw

    def test_0x_prefix_and_case(self):
        assert _hex_to_bytes("0xDEADBEEF") == b"\xde\xad\xbe\xef"
        assert _hex_to_bytes("DEADbeef") == b"\xde\xad\xbe\xef"

    def test_odd_length_rejected(self):
        with pytest.raises(ValueError, match="even length"):
            _hex_to_bytes("abc")


# ---------------------------------------------------------------------------
# Tool registration — every tool the README documents is present
# ---------------------------------------------------------------------------

_EXPECTED_TOOLS = {
    "start_emulation", "shutdown_emulation", "get_status",
    "list_supported_backends", "read_register", "write_register",
    "read_registers", "list_registers", "read_memory", "write_memory",
    "read_word", "write_word", "set_breakpoint", "remove_breakpoint",
    "list_breakpoints", "list_intercepts", "cont", "step", "stop",
    "inject_irq", "lookup_symbol", "lookup_address", "list_memory_regions",
    "disassemble", "set_watchpoint", "remove_watchpoint", "list_watchpoints",
    "read_string", "get_args",
}


class TestToolRegistration:
    def test_all_expected_tools_registered(self):
        srv = build_server()
        names = {t.name for t in asyncio.run(srv.list_tools())}
        missing = _EXPECTED_TOOLS - names
        assert not missing, f"missing tools: {sorted(missing)}"

    def test_list_supported_backends_via_client(self):
        srv = build_server()

        async def go():
            async with _connect(srv._mcp_server) as client:
                await client.initialize()
                return _result(await client.call_tool(
                    "list_supported_backends", {}))

        vals = asyncio.run(go())
        assert "unicorn" in vals and "ghidra" in vals


# ---------------------------------------------------------------------------
# End-to-end through the tool layer (needs the arm32 firmware)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _FW_PRESENT, reason="arm32 test firmware not built")
class TestToolEndToEnd:
    def test_read_write_word_endianness_and_disasm(self, monkeypatch):
        # The YAML memory file references firmware/test_uart.bin relatively,
        # so run from the arm32 dir (mirrors test_session.py).
        monkeypatch.chdir(ARM32_DIR)
        srv = build_server()

        async def go():
            async with _connect(srv._mcp_server) as client:
                await client.initialize()
                start = _result(await client.call_tool("start_emulation", {
                    "config_paths": _arm32_configs(),
                    "emulator": "unicorn",
                    "target_name": "srvtest",
                }))
                # write_word/read_word default to little-endian on cortex-m3
                await client.call_tool("write_word", {
                    "addr": 0x20001000, "value": 0x11223344})
                mem = _result(await client.call_tool("read_memory", {
                    "addr": 0x20001000, "size": 4}))
                word = _result(await client.call_tool("read_word", {
                    "addr": 0x20001000}))
                disasm = _result(await client.call_tool("disassemble", {
                    "count": 2}))
                await client.call_tool("shutdown_emulation", {})
                return start, mem, word, disasm

        start, mem, word, disasm = asyncio.run(go())
        assert start["arch"] == "cortex-m3"
        assert "session_id" in start       # multi-session: id returned
        assert mem == "44332211"          # little-endian byte order
        assert word == 0x11223344
        assert disasm and disasm[0]["mnemonic"]


class TestBearerAuth:
    """The HTTP bearer-token ASGI shim — pure, no real server needed."""

    @staticmethod
    def _drive(app, scope):
        sent = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(msg):
            sent.append(msg)

        asyncio.run(app(scope, receive, send))
        return sent

    def test_rejects_missing_token(self):
        reached = {"v": False}

        async def inner(scope, receive, send):
            reached["v"] = True

        app = bearer_auth_asgi(inner, "s3cret")
        sent = self._drive(app, {"type": "http", "headers": []})
        assert sent[0]["status"] == 401
        assert reached["v"] is False

    def test_rejects_wrong_token(self):
        async def inner(scope, receive, send):
            raise AssertionError("inner should not run")

        app = bearer_auth_asgi(inner, "s3cret")
        scope = {"type": "http",
                 "headers": [(b"authorization", b"Bearer nope")]}
        assert self._drive(app, scope)[0]["status"] == 401

    def test_accepts_correct_token(self):
        reached = {"v": False}

        async def inner(scope, receive, send):
            reached["v"] = True

        app = bearer_auth_asgi(inner, "s3cret")
        scope = {"type": "http",
                 "headers": [(b"authorization", b"Bearer s3cret")]}
        self._drive(app, scope)
        assert reached["v"] is True

    def test_rejects_websocket_without_token(self):
        # Default-deny: a websocket scope must NOT bypass auth.
        async def inner(scope, receive, send):
            raise AssertionError("inner should not run")

        app = bearer_auth_asgi(inner, "s3cret")
        sent = self._drive(app, {"type": "websocket", "headers": []})
        assert sent and sent[0]["type"] == "websocket.close"

    def test_lifespan_passthrough(self):
        # Non-request scopes (lifespan) must reach inner regardless of auth, or
        # the app's startup/shutdown (our SessionManager teardown) breaks.
        reached = {"v": False}

        async def inner(scope, receive, send):
            reached["v"] = True

        app = bearer_auth_asgi(inner, "s3cret")
        self._drive(app, {"type": "lifespan"})
        assert reached["v"] is True


@pytest.mark.skipif(not _FW_PRESENT, reason="arm32 test firmware not built")
class TestMultiSessionViaClient:
    def test_two_sessions_and_resources(self, monkeypatch):
        monkeypatch.chdir(ARM32_DIR)
        srv = build_server(max_sessions=4, port_base=18600)

        async def go():
            import json
            async with _connect(srv._mcp_server) as client:
                await client.initialize()
                a = _result(await client.call_tool("start_emulation", {
                    "config_paths": _arm32_configs(), "target_name": "alpha"}))
                b = _result(await client.call_tool("start_emulation", {
                    "config_paths": _arm32_configs(), "target_name": "beta"}))
                # Independent memory under distinct session_ids.
                await client.call_tool("write_word", {
                    "addr": 0x20001000, "value": 0xAAAAAAAA,
                    "session_id": a["session_id"]})
                await client.call_tool("write_word", {
                    "addr": 0x20001000, "value": 0x55555555,
                    "session_id": b["session_id"]})
                wa = _result(await client.call_tool("read_word", {
                    "addr": 0x20001000, "session_id": a["session_id"]}))
                wb = _result(await client.call_tool("read_word", {
                    "addr": 0x20001000, "session_id": b["session_id"]}))
                sessions = _result(await client.call_tool("list_sessions", {}))
                # Resources: the sessions list + per-session status template.
                sess_doc = json.loads(
                    (await client.read_resource("halu://sessions")
                     ).contents[0].text)
                status_doc = json.loads(
                    (await client.read_resource(
                        f"halu://session/{a['session_id']}/status")
                     ).contents[0].text)
                await client.call_tool("shutdown_emulation",
                                       {"session_id": a["session_id"]})
                await client.call_tool("shutdown_emulation",
                                       {"session_id": b["session_id"]})
                return wa, wb, sessions, sess_doc, status_doc

        wa, wb, sessions, sess_doc, status_doc = asyncio.run(go())
        assert wa == 0xAAAAAAAA and wb == 0x55555555   # independent
        assert len(sessions) == 2
        assert {s["session_id"] for s in sess_doc} == {
            s["session_id"] for s in sessions}
        assert status_doc["active"] is True
