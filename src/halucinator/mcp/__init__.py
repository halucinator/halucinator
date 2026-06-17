"""halucinator.mcp — MCP (Model Context Protocol) server for HALucinator.

Exposes HALucinator emulation primitives (memory, registers, breakpoints,
control flow, analysis) as MCP tools so an LLM client (Claude Desktop, Claude
Code, custom MCP clients) can drive firmware emulation interactively. The
server supervises one worker subprocess per session (see manager.py /
_worker.py), so several firmware images can run at once.

Run as a stdio server:
    halucinator-mcp
or:
    python -m halucinator.mcp

Run as a streamable-HTTP server (set --auth-token for remote use):
    halucinator-mcp --http --port 8765

Note: building/running the server needs the MCP SDK (Python 3.10+); the
session-layer primitives below import without it.
"""
from .session import HalucinatorSession, SessionError
from .manager import SessionManager

__all__ = ["HalucinatorSession", "SessionError", "SessionManager"]
