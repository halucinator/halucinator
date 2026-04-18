# Debugging HALucinator-Emulated Firmware

HALucinator exposes two independent debug interfaces that can be used
simultaneously or on their own:

- **GDB RSP server** (`--gdb-server`) — works with any GDB client: Ghidra,
  command-line GDB, IDA, Binary Ninja, VSCode's `cppdbg` extension, etc.
- **Debug Adapter Protocol server** (`--dap`) — works with the HALucinator
  VSCode extension for gview-based source-level debugging.

Both interfaces are **HAL-aware**: they transparently handle HAL intercepts
(where Python handlers replace firmware functions) so that the debug client
only stops at user-set breakpoints, not at every intercepted function call.

---

## GDB RSP Server (`--gdb-server`)

### Starting HALucinator with a GDB server

```sh
halucinator -c memory.yaml -c intercepts.yaml -c addrs.yaml --gdb-server
```

This starts a GDB remote serial protocol server on port **3333** (default).
To use a custom port:

```sh
halucinator -c config.yaml --gdb-server 4444
```

### Connecting with command-line GDB

```sh
$ gdb-multiarch
(gdb) target remote localhost:3333
(gdb) info registers
(gdb) x/10i $pc
(gdb) break *0x08001234
(gdb) continue
(gdb) si               # single-step one instruction
(gdb) p/x $r0          # print register in hex
```

### Connecting with Ghidra

1. Open the firmware in Ghidra and analyze it.
2. Open the Debugger tool: **Window → Debugger**.
3. Create a new GDB connection: use the **gdb** launcher.
4. Set the GDB executable to `gdb-multiarch`.
5. In the GDB console (once launched):
   ```
   target remote localhost:3333
   ```
6. Ghidra now has live registers, memory, breakpoints, and stepping, with PC
   highlighted in the disassembly view.

### Connecting with VSCode (via cppdbg)

`.vscode/launch.json`:

```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "HALucinator GDB",
      "type": "cppdbg",
      "request": "launch",
      "program": "${workspaceFolder}/firmware.elf",
      "MIMode": "gdb",
      "miDebuggerPath": "/usr/bin/gdb-multiarch",
      "miDebuggerServerAddress": "localhost:3333",
      "cwd": "${workspaceFolder}"
    }
  ]
}
```

### How HAL intercepts are handled

When the firmware calls an intercepted function (e.g. `HAL_UART_Transmit`),
Avatar2's watchman fires the Python handler, rewrites the return value and
PC, and resumes execution. The GDB server's `stop_filter` detects these
stops by PC address and suppresses them — the GDB client never sees the
intercept. Only user breakpoints produce visible stops.

### Limitations

- **One GDB client at a time.** QEMU's GDB stub is already held by
  Avatar2's internal `gdb-multiarch`; the `--gdb-server` re-exposes the
  target on a new port. That re-exposed port accepts a single connection.
- **Intercepted functions cannot be stepped into.** Stepping onto a HAL
  intercept address jumps to the return site — the function was replaced
  by Python, not executed.

---

## Debug Adapter Protocol (`--dap`)

### Starting HALucinator with a DAP server

```sh
halucinator -c memory.yaml -c intercepts.yaml -c addrs.yaml --dap
```

Default port is **34157**. To use a custom port:

```sh
halucinator -c config.yaml --dap 12345
```

### Connecting with VSCode

The DAP server is designed for use with the HALucinator VSCode extension,
which integrates the gview disassembly viewer (Ghidra-based) with live
debugging.

`.vscode/launch.json`:

```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "HALucinator Debug",
      "type": "halucinator",
      "request": "attach",
      "host": "localhost",
      "port": 34157
    }
  ]
}
```

Start HALucinator first, then launch the debug session in VSCode. The DAP
server provides execution control (continue, step, next, finish),
breakpoint management (including HAL-specific breakpoints), register and
memory inspection, and stack trace viewing.

### DAP-specific features

Beyond standard DAP, HALucinator extends the protocol with:

- `listHalBreakpoints` — list registered HAL intercepts
- `setHalBreakpoints` — dynamically add/remove HAL intercepts
- `setBreakMode` — toggle between `cont` (stop at HAL intercepts) and
  `cont_through` (transparently skip HAL intercepts) continuation modes

---

## Running Both at Once

Both flags can be combined. The underlying Avatar2 target is shared, but
each debug interface operates independently:

```sh
halucinator -c config.yaml --dap --gdb-server
```

This is useful for running VSCode on the DAP port for gview-integrated
analysis while also connecting Ghidra on the GDB port. **Caveat:** using
*both clients simultaneously* to drive execution (e.g. one continues while
the other steps) is undefined — use one at a time.

---

## Ports Reference

| Flag | Default Port | Protocol | Typical Client |
|------|-------------:|----------|----------------|
| `--gdb-server [PORT]` | 3333 | GDB RSP | Ghidra, gdb-multiarch, IDA, cppdbg |
| `--dap [PORT]` | 34157 | Debug Adapter Protocol | HALucinator VSCode extension |
| `-p / --gdb_port` | 1234 | GDB RSP (QEMU internal) | Avatar2 only — do not connect externally |

`--gdb_port` controls the port QEMU's internal GDB stub listens on, which
Avatar2 connects to as a client. Do not connect external debuggers to that
port; use `--gdb-server` instead.

---

## Troubleshooting

**"GDBProtocol was unable to connect" on startup.** Avatar2's internal GDB
connection to QEMU timed out (5s default). Usually transient on slow
hosts — re-run. The test suite retries up to 3 times automatically.

**Ghidra shows register values but stepping does nothing.** Check that
HALucinator was started without `-d` (the debug shell holds the target
stopped and can conflict).

**Breakpoint set in GDB is never hit.** The function may be intercepted.
Use GDB's `monitor` command (once implemented) or check
`tmp/<name>/stats.yaml` to see which addresses are registered as HAL
intercepts. Set your breakpoint after the intercept instead of on it.

**Debug session stuck on a HAL intercept address.** The `stop_filter` only
suppresses stops at intercept addresses. If you explicitly set a GDB
breakpoint on an intercept address, the stop is not suppressed. Remove
the breakpoint or use a different address.
