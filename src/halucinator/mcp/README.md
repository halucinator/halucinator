<!-- Copyright 2026 Christopher Wright -->

# halucinator-mcp — MCP server for HALucinator

`halucinator-mcp` exposes HALucinator firmware emulation as an
[MCP](https://modelcontextprotocol.io/) server, so an LLM client (Claude
Desktop, Claude Code, custom MCP clients) can drive emulation interactively —
read/write registers and memory, set breakpoints and watchpoints, inject IRQs,
look up symbols, disassemble, and step or continue execution — across **several
firmware images at once**.

## Architecture

The server owns a **SessionManager**, not a single emulator. Each
`start_emulation` spawns a worker subprocess (`python -m
halucinator.mcp._worker`) that owns exactly one emulation session; every tool
call is proxied to the right worker over a line-delimited JSON-RPC pipe. This
process-per-session model is what makes concurrent firmware images possible:
HALucinator keeps process-wide global state (the bp-handler intercept tables,
the `peripheral_server` zmq sockets bound to fixed `ipc://` paths, class-level
peripheral buffers), so two sessions cannot safely share one interpreter.
Isolation also buys crash containment (a unicorn segfault on bad firmware takes
down only its worker) and a hard-kill escape for wedged firmware.

```
MCP client ──stdio/http──> halucinator-mcp ──JSON-RPC pipes──> worker (session "alpha")
                           (SessionManager)                └──> worker (session "beta")
```

Modules: `server.py` (FastMCP tools/resources), `manager.py` (SessionManager +
worker supervision), `_worker.py` (per-session driver), `session.py` (the
single-session emulation handle each worker runs), `_codec.py` (hex + JSON-RPC
wire codec).

## Install

The MCP layer needs **Python 3.10+** (the MCP SDK's floor); the rest of
HALucinator runs on 3.9. Use a dedicated virtualenv:

```sh
python3.10 -m venv .venv-mcp
.venv-mcp/bin/pip install -e '.[mcp]'      # mcp[cli] + capstone
# Plus HALucinator's own runtime deps (not pulled by the extra):
.venv-mcp/bin/pip install -r src/requirements.txt
.venv-mcp/bin/pip install -e deps/avatar2/ --no-deps
```

The `requirements.txt` pin of `setuptools<81` provides the `distutils` shim
that the forked `avatar2` imports, so 3.10–3.12 all work; 3.10 or 3.11 are the
safest choices.

## Run

### Stdio (recommended; what Claude Desktop / Claude Code expect)

```sh
halucinator-mcp
```

For Claude Code, add `.mcp.json` in your project root (point `command` at the
venv binary so the right interpreter + deps are used):

```json
{
  "mcpServers": {
    "halucinator": {
      "command": "/abs/path/to/.venv-mcp/bin/halucinator-mcp"
    }
  }
}
```

Claude Desktop: the same block under `mcpServers` in
`~/Library/Application Support/Claude/claude_desktop_config.json`.

### Streamable HTTP (remote / multi-client)

```sh
halucinator-mcp --http --host 0.0.0.0 --port 8765 --auth-token "$TOKEN"
```

Client URL: `http://<host>:8765/mcp`, with header
`Authorization: Bearer <TOKEN>`.

**Security:** the tools read and write firmware memory and registers — anyone
who can reach the port has full control of the emulated target. The default
bind is `127.0.0.1`. Binding a non-loopback host **without** `--auth-token`
(or the `HALUCINATOR_MCP_TOKEN` env var) logs a loud warning; always set a
token for remote use, behind TLS / a tunnel.

CLI: `--max-sessions N` (default 4) caps concurrent sessions; `--port-base`
(default 5600) sets the base for each session's peripheral_server rx/tx port
pair (two consecutive ports per session).

## Supported backends

| Backend | Notes |
|---|---|
| `unicorn` | Fast, in-process. ARM/ARM64/MIPS/PPC/PPC64/x86. Default. |
| `ghidra`  | Slower, but useful for archs the QEMU build doesn't ship. |

`avatar2`, `qemu`, and `renode` need a subprocess + dispatch loop; drive them
via the `halucinator` CLI directly.

## Sessions

`start_emulation(...)` returns a `session_id`. **Every tool takes an optional
`session_id`** — omit it when exactly one session is active (it resolves to
that one); pass it explicitly to disambiguate when several are running.
`list_sessions()` enumerates them. Resources mirror this:
`halu://sessions` (the list) and `halu://session/{session_id}/status`.

## Concurrency contract

A backend isn't thread-safe while it's executing, so **while a non-blocking
`cont()` is in flight, register/memory queries are rejected** with a
`SessionError` — call `stop()` first. `get_status()` stays available (it
reports `running: true` and `pc: null` without touching the backend). For long
runs the pattern is: `cont(blocking=False)` → poll `get_status()` →
`stop()` → inspect. A blocking `cont(timeout=N)` pauses the firmware and
returns `state="timeout"` if it doesn't halt in time.

## Tools

### Lifecycle
| Tool | Description |
|---|---|
| `start_emulation(config_paths, emulator='unicorn', target_name='halucinator', session_id=None)` | Spawn a worker, load YAML config(s), register HAL intercepts. Returns metadata incl. `session_id`. Does NOT advance the firmware. |
| `shutdown_emulation(session_id=None)` | Tear down a session + its worker/ports. |
| `list_sessions()` | Active sessions: id, target, emulator, arch, state, ports, alive. |
| `get_status(session_id=None)` | Active flag, PC, last run state, running/exited flags. |
| `list_supported_backends()` | In-process backends. |

### Memory + registers
| Tool | Description |
|---|---|
| `read_register(name)` / `write_register(name, value)` | One register. |
| `read_registers()` / `list_registers()` | Snapshot / names. |
| `read_memory(addr, size)` / `write_memory(addr, hex_data)` | Up to 65 536 bytes; hex strings. |
| `read_word(addr, size=4)` / `write_word(addr, value, size=4)` | Endian-aware word (big on MIPS/PPC, little on ARM/x86); `size` 1/2/4/8. |

### Breakpoints + intercepts
| Tool | Description |
|---|---|
| `set_breakpoint(addr)` → `bp_id` / `remove_breakpoint(bp_id)` / `list_breakpoints()` | Debug breakpoints. |
| `list_intercepts()` | HAL intercepts from the YAML; bp_id, addr, function, hit count. |

### Execution control
| Tool | Description |
|---|---|
| `cont(timeout=30, blocking=True)` | Resume until breakpoint/intercept/exit. `blocking=False` returns at once; poll `get_status`. |
| `step()` | Single instruction (unicorn). |
| `stop()` | Pause an in-progress non-blocking cont. |
| `inject_irq(irq_num)` | Hardware IRQ (Cortex-M / ARM). |

### Symbols + analysis
| Tool | Description |
|---|---|
| `lookup_symbol(name)` / `lookup_address(addr)` | Forward / reverse symbol lookup. |
| `list_memory_regions()` | Configured regions. |
| `disassemble(addr=PC, count=8, thumb=null)` | capstone, arch-aware. |
| `set_watchpoint(addr, write=True, read=False, size=4)` → `bp_id` / `remove_watchpoint(bp_id)` / `list_watchpoints()` | Halt on memory access. |
| `read_string(addr, max_len=256)` | NUL-terminated string. |
| `get_args(count)` | First `count` function args per the target ABI (use at a function entry / intercept). |

(Every tool above also accepts `session_id`.)

## Example session

```jsonc
> start_emulation({ config_paths: [
    "test/multi_arch/arm32/test_uart_config.yaml",
    "test/multi_arch/arm32/test_uart_addrs.yaml",
    "test/multi_arch/arm32/test_uart_memory.yaml" ] })
{ session_id: "halucinator-1", arch: "cortex-m3", entry_addr: 0x08000113, ... }

> lookup_symbol({ name: "uart_init" })            // 0x080000c9
> set_breakpoint({ addr: 0x080000c9 })            // 4  (bp_id)
> cont({ timeout: 5 })                            // { state: "debug_bp", pc: 0x080000c8, bp_id: 4 }
> read_register({ name: "r0" })                   // 0x40000000  (uart_id arg)
> read_word({ addr: 0x20000000 })                 // little-endian word
> shutdown_emulation({})
```

## Tests

```sh
.venv-mcp/bin/python -m pytest test/pytest/mcp_server/ -q     # 3.10+: all
PYTHONPATH=src python3 -m pytest test/pytest/mcp_server/ -q   # 3.9: SDK tests skip
```

`test_session.py`, `test_codec.py`, `test_worker.py`, `test_manager.py` are
SDK-free and run on 3.9; `test_server.py` self-skips (`importorskip`) without
the MCP SDK.

## Notes

- One worker process per session; kill `halucinator-mcp` and all workers are
  reaped (atexit + SessionManager teardown).
- A `bp_handler` that blocks on external I/O (e.g. `UARTPublisher.read(block=True)`)
  parks the worker inside Python; `stop()` can't interrupt that. Use
  `cont(timeout=N)` and design handlers accordingly. A worker that ignores its
  own `emu_stop` timeout is force-killed and the session marked crashed.
