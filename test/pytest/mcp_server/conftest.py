# Copyright 2026 Christopher Wright

"""Collection notes for the MCP test package.

`test_session.py` imports only `halucinator.mcp.session`, which has no
dependency on the MCP SDK, so it runs on the project's baseline Python 3.9.
`test_server.py` and `test_manager.py` import the MCP SDK (FastMCP), which
requires Python 3.10+. Each of those modules guards itself with
`pytest.importorskip("mcp")` at the top, so on 3.9 they skip cleanly rather
than erroring out collection. (A `collect_ignore` here is unreliable for a
package-dir conftest, so the per-module importorskip is the real guard.)
"""
