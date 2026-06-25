# Copyright 2026 Christopher Wright

"""FastMCP server exposing HALucinator emulation as MCP tools + resources.

Run with stdio (the transport Claude Desktop / Claude Code expect when
they spawn a server from claude_desktop_config.json / .mcp.json):

    halucinator-mcp

Run as a streamable-HTTP server (useful when the emulator runs on a
beefy Linux host and the Claude client lives elsewhere):

    halucinator-mcp --http --port 8765

The server owns a SessionManager, not a single session: each
`start_emulation` spawns a worker subprocess driving one firmware image, so
several can run at once (see manager.py / _worker.py). Every tool takes an
optional `session_id`; when exactly one session exists it may be omitted and
resolves to that session, so the common single-firmware case stays simple.
"""
from __future__ import annotations

import argparse
import hmac
import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .session import SessionError, SUPPORTED_BACKENDS, DEFAULT_CONT_TIMEOUT
from .manager import SessionManager

# Import FastMCP at module scope. This module uses
# `from __future__ import annotations`, so tool annotations are strings that
# FastMCP evaluates with eval_str=True against this module's globals — every
# annotation type below (List/Dict/Any/Optional/int/str/bool/float) therefore
# has to be importable here. We still want `import halucinator.mcp.server` to
# succeed without the SDK (the session-layer tests import the package on 3.9),
# so fall back to a stand-in and raise a friendly error from build_server().
try:
    from mcp.server.fastmcp import FastMCP
    _MCP_IMPORT_ERROR: Optional[ImportError] = None
except ImportError as _exc:  # pragma: no cover — exercised only without the SDK
    FastMCP = None  # type: ignore[assignment,misc]
    _MCP_IMPORT_ERROR = _exc

log = logging.getLogger(__name__)

# Hex codec lives in _codec (shared with the worker/manager wire). Re-exported
# under the existing private names the tests use.
from ._codec import bytes_to_hex as _bytes_to_hex  # noqa: E402,F401
from ._codec import hex_to_bytes as _hex_to_bytes  # noqa: E402,F401


def build_server(name: str = "halucinator", max_sessions: int = 4,
                 port_base: int = 5600) -> Any:
    """Build a FastMCP server with all HALucinator tools registered.

    A single SessionManager is created here and captured by the tools and
    resources via closure (so resources, which don't get a request Context,
    can still reach it). The lifespan tears every worker down on server stop.
    """
    if FastMCP is None:  # pragma: no cover — install hint
        raise SessionError(
            "The MCP SDK isn't installed. "
            "Install it with: pip install 'mcp[cli]>=1.0'"
        ) from _MCP_IMPORT_ERROR

    manager = SessionManager(max_sessions=max_sessions, port_base=port_base)

    @asynccontextmanager
    async def _lifespan(_server: Any):
        try:
            yield {}
        finally:
            manager.shutdown_all()

    mcp = FastMCP(name, lifespan=_lifespan)

    # ------------------------------------------------------------------
    # Lifecycle tools
    # ------------------------------------------------------------------

    @mcp.tool()
    def start_emulation(
        config_paths: List[str],
        emulator: str = "unicorn",
        target_name: str = "halucinator",
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Load HALucinator YAML config files, spawn a worker, and prepare a
        backend. Returns session metadata including a `session_id` to pass to
        later tools (omit session_id on other tools when only one session is
        active).

        config_paths: one or more paths to halucinator config YAMLs
            (machine, memories, intercepts, addrs). Same files you'd pass to
            `halucinator -c`.
        emulator: backend to drive in-process — 'unicorn' (fast,
            ARM/ARM64/MIPS/PPC/x86) or 'ghidra'. avatar2/qemu/renode aren't
            drivable from the in-process worker.
        target_name: short name for the tmp/<name>/ output dir.
        session_id: optional explicit id; auto-generated if omitted.

        Does NOT advance the firmware — call cont() / step() to run.
        """
        return manager.create(config_paths, emulator=emulator,
                              target_name=target_name, session_id=session_id)

    @mcp.tool()
    def shutdown_emulation(session_id: Optional[str] = None) -> Dict[str, Any]:
        """Tear down a session and release its worker + ports."""
        handle = manager.resolve(session_id)
        return manager.destroy(handle.session_id)

    @mcp.tool()
    def list_sessions() -> List[Dict[str, Any]]:
        """List active sessions: session_id, target_name, emulator, arch,
        state, ports, and whether the worker is alive."""
        return manager.list_sessions()

    @mcp.tool()
    def get_status(session_id: Optional[str] = None) -> Dict[str, Any]:
        """Return session state: active flag, PC, last run outcome,
        running/exited flags, last error. With no active session returns
        {active: False}."""
        if session_id is None and not manager.list_sessions():
            return {"active": False}
        return manager.call(session_id, "status")

    @mcp.tool()
    def list_supported_backends() -> List[str]:
        """Return the in-process backends this MCP server can drive."""
        return list(SUPPORTED_BACKENDS)

    # ------------------------------------------------------------------
    # Memory + register tools
    # ------------------------------------------------------------------

    @mcp.tool()
    def read_register(name: str, session_id: Optional[str] = None) -> int:
        """Read one architectural register by name. Common names: 'pc', 'sp',
        'lr', 'r0'..'r15' on ARM; arch-specific otherwise."""
        return manager.call(session_id, "read_register", name=name)

    @mcp.tool()
    def write_register(name: str, value: int,
                       session_id: Optional[str] = None) -> None:
        """Set an architectural register. Effective on next cont/step."""
        manager.call(session_id, "write_register", name=name, value=value)

    @mcp.tool()
    def read_registers(session_id: Optional[str] = None) -> Dict[str, int]:
        """Snapshot every architectural register the backend exposes."""
        return manager.call(session_id, "read_registers")

    @mcp.tool()
    def list_registers(session_id: Optional[str] = None) -> List[str]:
        """Names of registers this backend can read/write."""
        return manager.call(session_id, "list_registers")

    @mcp.tool()
    def read_memory(addr: int, size: int,
                    session_id: Optional[str] = None) -> str:
        """Read up to 65536 bytes from *addr*; returns a lowercase hex string
        (no 0x prefix). Use bytes.fromhex(...) on the client side."""
        return manager.call(session_id, "read_memory", addr=addr, size=size)

    @mcp.tool()
    def write_memory(addr: int, hex_data: str,
                     session_id: Optional[str] = None) -> bool:
        """Write *hex_data* (a hex string, optionally 0x-prefixed) to *addr*.
        Length must be 1..65536 bytes."""
        return manager.call(session_id, "write_memory", addr=addr,
                            data=hex_data)

    @mcp.tool()
    def read_word(addr: int, size: int = 4,
                  session_id: Optional[str] = None) -> int:
        """Read a *size*-byte word at *addr*, decoded with the target's
        endianness (big-endian on MIPS/PPC, little-endian on ARM/x86). size
        must be 1, 2, 4, or 8 (use 8 for ppc64/arm64)."""
        return manager.call(session_id, "read_word", addr=addr, size=size)

    @mcp.tool()
    def write_word(addr: int, value: int, size: int = 4,
                   session_id: Optional[str] = None) -> bool:
        """Write *value* as a *size*-byte word at *addr* using the target's
        endianness. *value* is masked to *size* bytes. size must be 1, 2, 4,
        or 8."""
        return manager.call(session_id, "write_word", addr=addr, value=value,
                            size=size)

    # ------------------------------------------------------------------
    # Breakpoints + intercepts
    # ------------------------------------------------------------------

    @mcp.tool()
    def set_breakpoint(addr: int, session_id: Optional[str] = None) -> int:
        """Install a debug breakpoint at *addr*; returns an opaque bp_id.
        cont() returns when execution reaches *addr*."""
        return manager.call(session_id, "set_breakpoint", addr=addr)

    @mcp.tool()
    def remove_breakpoint(bp_id: int,
                          session_id: Optional[str] = None) -> bool:
        """Remove a previously-installed debug breakpoint."""
        return manager.call(session_id, "remove_breakpoint", bp_id=bp_id)

    @mcp.tool()
    def list_breakpoints(session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Debug breakpoints installed via set_breakpoint."""
        return manager.call(session_id, "list_breakpoints")

    @mcp.tool()
    def list_intercepts(session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """HAL intercepts loaded from the YAML config. Each entry has bp_id,
        addr, function name, handler class, and hit count."""
        return manager.call(session_id, "list_intercepts")

    # ------------------------------------------------------------------
    # Execution control
    # ------------------------------------------------------------------

    @mcp.tool()
    def cont(timeout: float = DEFAULT_CONT_TIMEOUT, blocking: bool = True,
             session_id: Optional[str] = None) -> Dict[str, Any]:
        """Resume execution until a breakpoint, intercept, or exit.

        With blocking=True (default) returns when execution halts or *timeout*
        seconds elapse (then state='timeout'). With blocking=False returns
        immediately; poll get_status until running=False. Backend queries are
        rejected while a non-blocking cont runs — stop() first."""
        return manager.call(session_id, "cont", blocking=blocking,
                            timeout=timeout)

    @mcp.tool()
    def step(session_id: Optional[str] = None) -> Dict[str, Any]:
        """Single-step one instruction (unicorn; ghidra raises)."""
        return manager.call(session_id, "step")

    @mcp.tool()
    def stop(session_id: Optional[str] = None) -> Dict[str, Any]:
        """Pause an in-progress non-blocking cont(). Idempotent."""
        return manager.call(session_id, "stop")

    @mcp.tool()
    def inject_irq(irq_num: int, session_id: Optional[str] = None) -> bool:
        """Inject hardware interrupt *irq_num* (Cortex-M / ARM only)."""
        return manager.call(session_id, "inject_irq", irq_num=irq_num)

    # ------------------------------------------------------------------
    # Symbol + memory layout introspection
    # ------------------------------------------------------------------

    @mcp.tool()
    def lookup_symbol(name: str,
                      session_id: Optional[str] = None) -> Optional[int]:
        """Forward symbol lookup: returns address, or None if unknown."""
        return manager.call(session_id, "lookup_symbol", name=name)

    @mcp.tool()
    def lookup_address(addr: int,
                       session_id: Optional[str] = None) -> Optional[str]:
        """Reverse symbol lookup: returns the symbol containing *addr*."""
        return manager.call(session_id, "lookup_address", addr=addr)

    @mcp.tool()
    def list_memory_regions(session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Memory regions registered with the backend (including any
        Python-emulated peripheral stubs)."""
        return manager.call(session_id, "list_memory_regions")

    # ------------------------------------------------------------------
    # Analysis — disassembly, watchpoints, strings, call args
    # ------------------------------------------------------------------

    @mcp.tool()
    def disassemble(addr: Optional[int] = None, count: int = 8,
                    thumb: Optional[bool] = None,
                    session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Disassemble *count* instructions (1..256) starting at *addr*
        (defaults to PC). thumb overrides ARM/Thumb decoding on ARM. Returns
        addr, size, raw bytes (hex), mnemonic, op_str, and combined text."""
        return manager.call(session_id, "disassemble", addr=addr, count=count,
                            thumb=thumb)

    @mcp.tool()
    def set_watchpoint(addr: int, write: bool = True, read: bool = False,
                       size: int = 4,
                       session_id: Optional[str] = None) -> int:
        """Install a memory-access watchpoint over [addr, addr+size).
        Execution halts (cont returns) on read/write to the range."""
        return manager.call(session_id, "set_watchpoint", addr=addr,
                            write=write, read=read, size=size)

    @mcp.tool()
    def remove_watchpoint(bp_id: int,
                          session_id: Optional[str] = None) -> bool:
        """Remove a watchpoint previously installed via set_watchpoint."""
        return manager.call(session_id, "remove_watchpoint", bp_id=bp_id)

    @mcp.tool()
    def list_watchpoints(session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Watchpoints installed via set_watchpoint."""
        return manager.call(session_id, "list_watchpoints")

    @mcp.tool()
    def read_string(addr: int, max_len: int = 256,
                    session_id: Optional[str] = None) -> str:
        """Read a NUL-terminated string from *addr* (latin-1), reading at most
        *max_len* bytes (1..65536)."""
        return manager.call(session_id, "read_string", addr=addr,
                            max_len=max_len)

    @mcp.tool()
    def get_args(count: int, session_id: Optional[str] = None) -> List[int]:
        """Read the first *count* (0..16) function arguments per the target
        ABI. Meaningful at a function entry — e.g. a HAL intercept or a
        breakpoint on a prologue."""
        return manager.call(session_id, "get_args", count=count)

    # ------------------------------------------------------------------
    # Resources — read-only addressable views, backed by the manager closure
    # (resources don't receive a request Context, so the closure is how they
    # reach session state).
    # ------------------------------------------------------------------

    @mcp.resource("halu://sessions")
    def _resource_sessions() -> str:
        return json.dumps(manager.list_sessions(), indent=2)

    @mcp.resource("halu://session/{session_id}/status")
    def _resource_status(session_id: str) -> str:
        return json.dumps(manager.call(session_id, "status"), indent=2)

    return mcp


def bearer_auth_asgi(inner_app: Any, token: str) -> Any:
    """Wrap an ASGI app so every request must carry
    `Authorization: Bearer <token>`.

    Implemented as a plain ASGI shim (not an SDK auth provider) so it's
    insensitive to FastMCP's evolving auth API: it works against whatever
    `streamable_http_app()` returns. Default-deny: BOTH `http` and `websocket`
    request scopes are authenticated; only the `lifespan` scope (and any other
    non-request scope) passes through, so the app's startup/shutdown still
    runs. An unauthenticated http request gets 401; a websocket gets a policy
    close (1008).
    """
    expected = f"Bearer {token}"

    async def app(scope: Dict[str, Any], receive: Callable[..., Awaitable[Any]],
                  send: Callable[..., Awaitable[Any]]) -> None:
        stype = scope.get("type")
        if stype in ("http", "websocket"):
            headers = dict(scope.get("headers") or [])
            provided = headers.get(b"authorization", b"").decode("latin-1")
            # constant-time compare to avoid leaking the token via timing
            if not hmac.compare_digest(provided, expected):
                if stype == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                else:
                    await send({
                        "type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"text/plain"),
                                    (b"www-authenticate", b"Bearer")],
                    })
                    await send({"type": "http.response.body",
                                "body": b"401 Unauthorized\n"})
                return
        await inner_app(scope, receive, send)

    return app


def main(argv: Optional[List[str]] = None) -> int:
    """`halucinator-mcp` console-script entry point."""
    parser = argparse.ArgumentParser(
        prog="halucinator-mcp",
        description="MCP server exposing HALucinator emulation primitives "
                    "(memory, registers, breakpoints, control) as tools "
                    "for LLM clients (Claude Desktop, Claude Code, etc.).",
    )
    parser.add_argument(
        "--http", action="store_true",
        help="Use streamable-HTTP transport instead of stdio.",
    )
    parser.add_argument(
        "--host", default="127.0.0.1",
        help="HTTP host (default: 127.0.0.1). Binding 0.0.0.0 exposes "
             "memory/register read-write to the network — use --auth-token.",
    )
    parser.add_argument(
        "--port", type=int, default=8765,
        help="HTTP port (default: 8765).",
    )
    parser.add_argument(
        "--name", default="halucinator",
        help="MCP server name reported to clients.",
    )
    parser.add_argument(
        "--max-sessions", type=int, default=4,
        help="Max concurrent emulation sessions (default: 4).",
    )
    parser.add_argument(
        "--port-base", type=int, default=5600,
        help="Base port for per-session peripheral_server rx/tx pairs "
             "(default: 5600; each session uses two consecutive ports).",
    )
    parser.add_argument(
        "--auth-token", default=os.environ.get("HALUCINATOR_MCP_TOKEN"),
        help="Require this bearer token on every HTTP request (or set "
             "HALUCINATOR_MCP_TOKEN). Strongly recommended with --http, "
             "required in spirit when binding a non-loopback --host.",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable debug logging on stderr.",
    )
    args = parser.parse_args(argv)

    log_level = logging.DEBUG if args.verbose else logging.INFO
    # MCP stdio uses stdout for protocol frames; force logging to stderr.
    logging.basicConfig(
        level=log_level, stream=sys.stderr,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    )

    server = build_server(args.name, max_sessions=args.max_sessions,
                          port_base=args.port_base)
    if args.http:
        server.settings.host = args.host
        server.settings.port = args.port
        _is_loopback = args.host in ("127.0.0.1", "::1", "localhost")
        if not args.auth_token and not _is_loopback:
            log.warning(
                "SECURITY: serving on %s:%s with NO --auth-token. The MCP "
                "tools can read/write firmware memory and registers — anyone "
                "who can reach this port has full control. Set --auth-token "
                "(or HALUCINATOR_MCP_TOKEN), or bind 127.0.0.1.",
                args.host, args.port,
            )
        if args.auth_token:
            import uvicorn
            app = bearer_auth_asgi(server.streamable_http_app(), args.auth_token)
            log.info("HTTP bearer-token auth enabled.")
            uvicorn.run(app, host=args.host, port=args.port,
                        log_level=("debug" if args.verbose else "info"))
        else:
            server.run(transport="streamable-http")
    else:
        server.run()  # stdio
    return 0


if __name__ == "__main__":
    sys.exit(main())
