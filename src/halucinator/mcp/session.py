# Copyright 2026 Christopher Wright

"""HalucinatorSession — long-lived emulation handle for the MCP server.

One process owns at most one session at a time (kept simple for v1).
The session holds:
  * a parsed HalucinatorConfig (from one or more YAML files)
  * an instantiated HalBackend (unicorn / ghidra are in-process and
    drivable from the MCP request thread; avatar2 / qemu / renode
    require a subprocess + dispatch loop, so the in-process MCP only
    supports unicorn and ghidra in v1)
  * the registered HAL intercepts and any debug breakpoints the MCP
    client has installed at runtime
  * a worker thread that runs the cont/step blocking calls so
    individual MCP tool invocations stay responsive

The session is *not* thread-safe in the sense that two concurrent MCP
tool calls may race on the underlying backend; FastMCP serialises tool
dispatch in a single asyncio loop, so that's acceptable. The only
concurrency is between an in-flight cont() in the worker and a stop()
or query from the MCP request thread — those go through a small lock.
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple, Union

log = logging.getLogger(__name__)


# Backends that the MCP server can drive in-process. avatar2/qemu/renode
# need a subprocess + dispatch loop to actually advance the firmware;
# wiring that in would require keeping main.py's _emulate_with_*_backend
# infrastructure alive in a worker thread, which is out of scope for v1.
SUPPORTED_BACKENDS: Tuple[str, ...] = ("unicorn", "ghidra")

# Architectures HALucinator targets that are big-endian. Used to decode
# multi-byte words from memory the same way the disassembler does (see
# _capstone, which selects CS_MODE_BIG_ENDIAN for exactly these). Every
# other supported arch (arm, cortex-m3, arm64, x86) is little-endian.
_BIG_ENDIAN_ARCHS: Tuple[str, ...] = (
    "mips", "powerpc", "powerpc:MPC8XX", "ppc64",
)

# Default wall-clock budget for a blocking cont(); single source of truth so
# the session, the MCP tool, and the manager's read-timeout all agree.
DEFAULT_CONT_TIMEOUT: float = 30.0


class SessionError(RuntimeError):
    """Raised when a session-state precondition is violated.

    Examples: calling read_register before start_emulation, calling
    start_emulation while a session is already active, requesting a
    backend the MCP server can't drive in-process.
    """


@dataclass
class _RunResult:
    """Outcome of one cont()/step() call. Populated by the worker thread."""

    pc: int = 0
    state: str = "unknown"  # one of: stopped, running, exited, error
    bp_id: Optional[int] = None
    handler: Optional[str] = None
    error: Optional[str] = None


@dataclass
class HalucinatorSession:
    """Stateful emulation handle owned by the MCP server's lifespan."""

    config: Any = None  # HalucinatorConfig
    backend: Any = None  # HalBackend
    target_name: str = "halucinator"
    emulator: str = "unicorn"
    outdir: str = "tmp/mcp_session"
    rx_port: int = 5555
    tx_port: int = 5556
    debug_breakpoints: Dict[int, int] = field(default_factory=dict)
    watchpoints: Dict[int, int] = field(default_factory=dict)  # bp_id -> addr
    _intercept_addr_to_bp: Dict[int, int] = field(default_factory=dict)
    _periph_thread: Optional[threading.Thread] = None
    _running: bool = False
    _exited: bool = False
    _last_run: _RunResult = field(default_factory=_RunResult)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _worker: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @property
    def active(self) -> bool:
        return self.backend is not None

    def assert_active(self) -> None:
        if not self.active:
            raise SessionError(
                "No active emulation session. Call start_emulation first."
            )

    def _is_running(self) -> bool:
        """True while a non-blocking cont() is advancing the firmware on the
        worker thread."""
        return (
            self._running
            and self._worker is not None
            and self._worker.is_alive()
        )

    def assert_idle(self) -> None:
        """Reject backend-touching operations while a non-blocking cont() is
        in flight.

        The backend (unicorn in particular) is not thread-safe: reading a
        register or memory from the MCP request thread while emu_start() is
        spinning on the worker thread races C state and can crash or return
        garbage. A lock can't help — emu_start holds the engine for the whole
        run, and stop()/emu_stop is specifically designed to be the only safe
        cross-thread call. So the contract is: while running, only stop() and
        get_status() are valid; everything else must stop() first. Use
        cont(blocking=False) + poll get_status() for long runs.
        """
        if self._is_running():
            raise SessionError(
                "Emulation is running (non-blocking cont in progress); "
                "call stop() before querying or mutating state."
            )

    def start(
        self,
        config_paths: List[str],
        emulator: str = "unicorn",
        target_name: str = "halucinator",
        rx_port: int = 5555,
        tx_port: int = 5556,
        start_periph_server: bool = True,
    ) -> Dict[str, Any]:
        """Parse the given YAML config files, instantiate the backend,
        register memory regions and HAL intercepts. Does NOT start
        execution — the caller invokes cont() / step() to advance.
        """
        from halucinator.bp_handlers import intercepts
        from halucinator.backends.hal_backend import MemoryRegion
        from halucinator.hal_config import HalucinatorConfig

        if self.active:
            raise SessionError(
                "An emulation session is already active. "
                "Call shutdown_emulation first."
            )
        if emulator not in SUPPORTED_BACKENDS:
            raise SessionError(
                f"Backend {emulator!r} not supported by the in-process MCP. "
                f"Supported: {SUPPORTED_BACKENDS!r}. "
                f"For avatar2 / qemu / renode use the halucinator CLI directly."
            )
        if not config_paths:
            raise SessionError("config_paths must contain at least one YAML file")
        for path in config_paths:
            if not os.path.exists(path):
                raise SessionError(f"Config file not found: {path}")

        config = HalucinatorConfig()
        for path in config_paths:
            config.add_yaml(path)
        # Resolve symbol-named intercepts to bp_addrs and validate the
        # config before we start instantiating the backend (same step
        # the CLI's main() calls before emulate_binary).
        if not config.prepare_and_validate():
            raise SessionError(
                "Config validation failed; see halucinator log for details."
            )

        outdir = os.path.join("tmp", target_name)
        os.makedirs(os.path.join(outdir, "logs"), exist_ok=True)

        arch = config.machine.arch
        if emulator == "unicorn":
            from halucinator.backends.unicorn_backend import (
                UnicornBackend, _ARCH_MAP,
            )
            if arch not in _ARCH_MAP:
                raise SessionError(
                    f"UnicornBackend has no mapping for arch={arch!r}. "
                    f"Supported: {sorted(_ARCH_MAP.keys())!r}."
                )
            backend = UnicornBackend(arch=arch)
        elif emulator == "ghidra":
            from halucinator.backends.ghidra_backend import GhidraBackend
            backend = GhidraBackend(arch=arch)
        else:  # pragma: no cover — guarded above
            raise SessionError(f"Unsupported backend: {emulator!r}")

        # Register memory regions + init — identical across backends.
        for mem in config.memories.values():
            backend.add_memory_region(MemoryRegion(
                name=mem.name, base_addr=mem.base_addr, size=mem.size,
                permissions=mem.permissions or "rwx", file=mem.file,
            ))
        backend.init()

        backend.avatar = SimpleNamespace(output_directory=outdir, config=config)

        if config.machine.entry_addr is not None:
            backend.regs.pc = config.machine.entry_addr
        if config.machine.arch == "cortex-m3":
            if config.machine.init_sp is not None:
                backend.regs.sp = config.machine.init_sp
            if hasattr(backend, "set_vtor"):
                backend.set_vtor(config.machine.vector_base)
        elif config.machine.init_sp is not None:
            backend.regs.sp = config.machine.init_sp

        for intercept in config.intercepts:
            if intercept.bp_addr is not None:
                bp_id = intercepts.register_bp_handler(backend, intercept)
                self._intercept_addr_to_bp[intercept.bp_addr] = bp_id

        # Start the peripheral_server in the background so bp_handlers
        # that publish over zmq (UARTPublisher, GPIO, etc.) work the
        # same way they do under the CLI's emulate_binary path. Most
        # MCP clients don't care about the zmq endpoints — they observe
        # peripheral I/O through tool return values and the hal_log
        # output instead — but the publishers themselves still need a
        # context to bind. Disable via start_periph_server=False if
        # the caller wants the ports left free.
        if start_periph_server:
            try:
                from halucinator.peripheral_models import (
                    peripheral_server as periph_server,
                )
                periph_server.start(rx_port, tx_port, backend)
                t = threading.Thread(
                    target=periph_server.run_server,
                    daemon=True, name="mcp-periph",
                )
                t.start()
                self._periph_thread = t
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "MCP: peripheral_server failed to start (%s); "
                    "publishing bp_handlers may raise.", exc,
                )

        self.config = config
        self.backend = backend
        self.target_name = target_name
        self.emulator = emulator
        self.outdir = outdir
        self.rx_port = rx_port
        self.tx_port = tx_port
        self._running = False
        self._exited = False
        self._last_run = _RunResult(pc=backend.read_register("pc"),
                                    state="stopped")
        return {
            "target_name": target_name,
            "emulator": emulator,
            "arch": config.machine.arch,
            "entry_addr": config.machine.entry_addr,
            "init_sp": config.machine.init_sp,
            "memory_regions": [
                {"name": m.name, "base_addr": m.base_addr, "size": m.size,
                 "permissions": m.permissions, "emulate_required":
                 getattr(m, "emulate_required", False)}
                for m in config.memories.values()
            ],
            "intercepts": len(self._intercept_addr_to_bp),
            "pc": self._last_run.pc,
        }

    def shutdown(self) -> Dict[str, Any]:
        """Tear down the backend and forget all state."""
        from halucinator.bp_handlers import intercepts as ic
        out = {"was_active": self.active}
        if self._worker is not None and self._worker.is_alive():
            from halucinator.peripheral_models.uart import UARTPublisher
            UARTPublisher.abort_blocking.set()
            try:
                self.backend.stop()
            except Exception:  # noqa: BLE001
                pass
            self._worker.join(timeout=2.0)
        if self._periph_thread is not None:
            try:
                from halucinator.peripheral_models import (
                    peripheral_server as periph_server,
                )
                # Flip the stop flag, wait for the run_server poller
                # loop to exit cleanly, *then* close the sockets and
                # null the globals. Closing first while the poll is
                # still running raises ZMQError in the worker thread.
                periph_server.stop()
                self._periph_thread.join(timeout=1.0)
                for attr in ("__RX_SOCKET__", "__TX_SOCKET__"):
                    sock = getattr(periph_server, attr, None)
                    if sock is not None:
                        try:
                            sock.close(linger=0)
                        except Exception:  # noqa: BLE001
                            pass
                        setattr(periph_server, attr, None)
                periph_server.__rx_socket__ = None
                periph_server.__tx_socket__ = None
                # Re-arm for the next start() call.
                periph_server.__STOP_SERVER = False
            except Exception:  # noqa: BLE001
                pass
            self._periph_thread = None
        if self.backend is not None:
            try:
                self.backend.shutdown()
            except Exception:  # noqa: BLE001
                pass
        # Forget any global state the intercepts module + hal_stats
        # accumulated during this session — otherwise a follow-up
        # start() (or another test in the same process) would see
        # stale bp handlers and hit counters.
        from halucinator import hal_stats
        ic.bp2handler_lut.clear()
        ic.addr2bp_lut.clear()
        ic.debugging_bps.clear()
        ic.watchpoint_bps.clear()
        ic.initalized_classes.clear()  # sic: name typo'd in upstream
        hal_stats.stats.clear()
        # Reset peripheral_models that accumulate per-firmware state.
        try:
            from halucinator.peripheral_models.uart import UARTPublisher
            UARTPublisher.rx_buffers.clear()
            # Worker is dead by now; re-arm blocking reads for the next session.
            UARTPublisher.abort_blocking.clear()
        except Exception:  # noqa: BLE001
            pass
        self.config = None
        self.backend = None
        self.debug_breakpoints.clear()
        self.watchpoints.clear()
        self._intercept_addr_to_bp.clear()
        self._running = False
        self._exited = False
        self._last_run = _RunResult()
        self._worker = None
        return out

    # ------------------------------------------------------------------
    # State queries
    # ------------------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        if not self.active:
            return {"active": False}
        running = self._is_running()
        # get_status must stay callable while a non-blocking cont() runs (it's
        # how the client polls for completion) — but reading the backend PC
        # mid-emu_start races the worker thread. While running, report
        # pc=None and skip the backend touch entirely.
        pc: Optional[int] = None
        err: Optional[str] = None
        if not running:
            with self._lock:
                try:
                    pc = self.backend.read_register("pc")
                except Exception as exc:  # noqa: BLE001
                    pc = -1
                    err = str(exc)
        # Snapshot _last_run once: the worker thread may reassign it
        # concurrently, and reading its fields individually off self could
        # mix fields from two different results.
        lr = self._last_run
        return {
            "active": True,
            "emulator": self.emulator,
            "arch": self.config.machine.arch,
            "running": running,
            "exited": self._exited,
            "pc": pc,
            "last_run_state": lr.state,
            "last_run_bp_id": lr.bp_id,
            "last_run_handler": lr.handler,
            "error": err,
        }

    def list_intercepts(self) -> List[Dict[str, Any]]:
        from halucinator.bp_handlers import intercepts
        from halucinator import hal_stats
        self.assert_active()
        out: List[Dict[str, Any]] = []
        for bp_id, info in intercepts.bp2handler_lut.items():
            stat = hal_stats.stats.get(bp_id, {})
            out.append({
                "bp_id": bp_id,
                "addr": getattr(info, "addr", None),
                "function": stat.get("function"),
                "class": info.cls.__class__.__name__
                if hasattr(info, "cls") else None,
                "hit_count": stat.get("count", 0),
                "active": stat.get("active", True),
            })
        return out

    def list_breakpoints(self) -> List[Dict[str, Any]]:
        self.assert_active()
        return [
            {"bp_id": bp_id, "addr": addr}
            for addr, bp_id in self.debug_breakpoints.items()
        ]

    def list_memory_regions(self) -> List[Dict[str, Any]]:
        self.assert_active()
        return [
            {"name": m.name, "base_addr": m.base_addr, "size": m.size,
             "permissions": m.permissions,
             "emulate_required": getattr(m, "emulate_required", False),
             "file": m.file}
            for m in self.config.memories.values()
        ]

    def lookup_symbol(self, name: str) -> Optional[int]:
        self.assert_active()
        return self.config.get_addr_for_symbol(name)

    def lookup_address(self, addr: int) -> Optional[str]:
        self.assert_active()
        try:
            return self.config.get_symbol_name(addr)
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------
    # Memory + registers
    # ------------------------------------------------------------------

    def read_register(self, name: str) -> int:
        self.assert_active()
        self.assert_idle()
        with self._lock:
            return self.backend.read_register(name)

    def write_register(self, name: str, value: int) -> None:
        self.assert_active()
        self.assert_idle()
        with self._lock:
            self.backend.write_register(name, value)

    def list_registers(self) -> List[str]:
        self.assert_active()
        self.assert_idle()
        return list(self.backend.list_registers())

    def read_registers(self) -> Dict[str, int]:
        self.assert_active()
        self.assert_idle()
        out: Dict[str, int] = {}
        with self._lock:
            for name in self.backend.list_registers():
                try:
                    out[name] = self.backend.read_register(name)
                except Exception:  # noqa: BLE001
                    pass
        return out

    def read_memory(self, addr: int, size: int) -> bytes:
        if size <= 0 or size > 0x10000:
            raise SessionError("size must be in (0, 65536]")
        self.assert_active()
        self.assert_idle()
        with self._lock:
            data = self.backend.read_memory(addr, 1, size, raw=True)
        return bytes(data) if not isinstance(data, bytes) else data

    def write_memory(self, addr: int, data: bytes) -> bool:
        if not data:
            raise SessionError("data must not be empty")
        if len(data) > 0x10000:
            raise SessionError("data must be <= 65536 bytes")
        self.assert_active()
        self.assert_idle()
        with self._lock:
            return bool(self.backend.write_memory(
                addr, 1, data, len(data), raw=True
            ))

    def byteorder(self) -> str:
        """The target's byte order ('big' or 'little'), derived from the
        machine arch — used to (de)serialise multi-byte words."""
        self.assert_active()
        return (
            "big" if self.config.machine.arch in _BIG_ENDIAN_ARCHS
            else "little"
        )

    def read_word(self, addr: int, size: int = 4) -> int:
        """Read a *size*-byte word at *addr* using the target's endianness.
        size must be 1, 2, 4, or 8 (8 for 64-bit targets like ppc64/arm64)."""
        if size not in (1, 2, 4, 8):
            raise SessionError("size must be 1, 2, 4, or 8")
        order = self.byteorder()
        return int.from_bytes(self.read_memory(addr, size), order)

    def write_word(self, addr: int, value: int, size: int = 4) -> bool:
        """Write *value* as a *size*-byte word at *addr* using the target's
        endianness. *value* is masked to *size* bytes (wraps, never
        OverflowError). size must be 1, 2, 4, or 8."""
        if size not in (1, 2, 4, 8):
            raise SessionError("size must be 1, 2, 4, or 8")
        order = self.byteorder()
        masked = value & ((1 << (size * 8)) - 1)
        return self.write_memory(addr, masked.to_bytes(size, order))

    # ------------------------------------------------------------------
    # Breakpoints
    # ------------------------------------------------------------------

    def set_breakpoint(self, addr: int) -> int:
        self.assert_active()
        self.assert_idle()
        with self._lock:
            bp_id = self.backend.set_breakpoint(addr)
        self.debug_breakpoints[addr] = bp_id
        return bp_id

    def remove_breakpoint(self, bp_id: int) -> bool:
        self.assert_active()
        self.assert_idle()
        with self._lock:
            try:
                self.backend.remove_breakpoint(bp_id)
            except Exception:  # noqa: BLE001
                return False
        for addr, b in list(self.debug_breakpoints.items()):
            if b == bp_id:
                del self.debug_breakpoints[addr]
        return True

    # ------------------------------------------------------------------
    # Execution control
    # ------------------------------------------------------------------

    def cont(self, blocking: bool = True,
             timeout: float = DEFAULT_CONT_TIMEOUT) -> Dict[str, Any]:
        """Resume execution. By default we block up to *timeout* seconds
        for the firmware to hit a breakpoint or exit. With blocking=False
        we kick off a worker thread and return immediately — the caller
        can poll status()/wait() to find out what happened."""
        self.assert_active()
        if self._worker is not None and self._worker.is_alive():
            raise SessionError("Already running. Call stop() first.")
        if self._exited:
            raise SessionError(
                "Emulation has exited; call shutdown_emulation + "
                "start_emulation to start a new run."
            )

        # Fresh run: make sure blocking UART reads block normally (a prior
        # stop()/timeout may have set the abort flag to unpark a leaked read).
        from halucinator.peripheral_models.uart import UARTPublisher
        UARTPublisher.abort_blocking.clear()
        self._running = True
        self._last_run = _RunResult(state="running")

        def _runner() -> None:
            from halucinator.bp_handlers import intercepts
            try:
                self._dispatch_loop(intercepts)
            except Exception as exc:  # noqa: BLE001
                self._last_run.error = str(exc)
                self._last_run.state = "error"
                log.exception("MCP dispatch loop crashed")
            finally:
                self._running = False

        self._worker = threading.Thread(
            target=_runner, name="mcp-cont", daemon=True
        )
        self._worker.start()
        if blocking:
            self._worker.join(timeout=timeout)
            if self._worker.is_alive():
                # Timed out — pause and let the caller decide. Unpark any
                # worker parked in a blocking UART read so backend.stop() can
                # take effect and the dispatch thread can exit (otherwise it
                # spins forever and leaks into the next run).
                UARTPublisher.abort_blocking.set()
                try:
                    self.backend.stop()
                except Exception:  # noqa: BLE001
                    pass
                self._worker.join(timeout=2.0)
                if self._last_run.state == "running":
                    self._last_run.state = "timeout"
        return self._snapshot_run()

    def step(self) -> Dict[str, Any]:
        """Single-step one instruction (where the backend supports it)."""
        self.assert_active()
        if self._worker is not None and self._worker.is_alive():
            raise SessionError("Already running. Call stop() first.")
        if self._exited:
            raise SessionError(
                "Emulation has exited; call shutdown_emulation + "
                "start_emulation to start a new run."
            )
        with self._lock:
            try:
                self.backend.step()
                pc = self.backend.read_register("pc")
            except NotImplementedError as exc:
                raise SessionError(str(exc)) from None
        self._last_run = _RunResult(pc=pc, state="stepped")
        return self._snapshot_run()

    def stop(self) -> Dict[str, Any]:
        """Pause a running cont(). Idempotent."""
        self.assert_active()
        # Unpark a worker parked in a blocking UART read so it can exit.
        from halucinator.peripheral_models.uart import UARTPublisher
        UARTPublisher.abort_blocking.set()
        try:
            self.backend.stop()
        except Exception:  # noqa: BLE001
            pass
        if self._worker is not None and self._worker.is_alive():
            self._worker.join(timeout=2.0)
        # Only declare the engine idle if the worker thread actually exited.
        # A firmware that ignores emu_stop (tight loop / blocked in a Python
        # bp_handler) leaves the thread alive; clearing _running there would
        # let assert_idle pass and let queries race the live engine. Leave
        # _running set so queries stay rejected until the worker is killed
        # (the manager force-kills the whole worker process on read timeout).
        if not (self._worker is not None and self._worker.is_alive()):
            self._running = False
        return self._snapshot_run()

    def inject_irq(self, irq_num: int) -> bool:
        self.assert_active()
        self.assert_idle()
        with self._lock:
            try:
                self.backend.inject_irq(irq_num)
            except NotImplementedError as exc:
                raise SessionError(str(exc)) from None
            except Exception as exc:  # noqa: BLE001
                raise SessionError(f"inject_irq failed: {exc}") from None
        return True

    # ------------------------------------------------------------------
    # Analysis helpers (read-only inspection for LLM-driven workflows)
    # ------------------------------------------------------------------

    def set_watchpoint(
        self, addr: int, write: bool = True, read: bool = False,
        size: int = 4,
    ) -> int:
        """Install a memory-access watchpoint; execution halts when the
        firmware reads/writes the watched range. Returns an opaque bp_id."""
        self.assert_active()
        self.assert_idle()
        if not write and not read:
            raise SessionError("watchpoint must watch reads, writes, or both")
        with self._lock:
            try:
                bp_id = self.backend.set_watchpoint(
                    addr, write=write, read=read, size=size,
                )
            except NotImplementedError as exc:
                raise SessionError(str(exc)) from None
        self.watchpoints[bp_id] = addr
        return bp_id

    def remove_watchpoint(self, bp_id: int) -> bool:
        self.assert_active()
        self.assert_idle()
        with self._lock:
            try:
                self.backend.remove_watchpoint(bp_id)
            except Exception:  # noqa: BLE001
                return False
        self.watchpoints.pop(bp_id, None)
        return True

    def list_watchpoints(self) -> List[Dict[str, Any]]:
        self.assert_active()
        return [
            {"bp_id": bp_id, "addr": addr}
            for bp_id, addr in self.watchpoints.items()
        ]

    def read_string(self, addr: int, max_len: int = 256) -> str:
        """Read a NUL-terminated string from *addr* (latin-1 decoded)."""
        if max_len <= 0 or max_len > 0x10000:
            raise SessionError("max_len must be in (0, 65536]")
        self.assert_active()
        self.assert_idle()
        with self._lock:
            return self.backend.read_string(addr, max_len)

    def get_args(self, count: int) -> List[int]:
        """Read the first *count* function arguments per the target ABI.
        Meaningful when stopped at a function entry (e.g. a HAL intercept
        or a breakpoint on a function prologue)."""
        if count < 0 or count > 16:
            raise SessionError("count must be in [0, 16]")
        self.assert_active()
        self.assert_idle()
        with self._lock:
            return [int(self.backend.get_arg(i)) for i in range(count)]

    def disassemble(
        self, addr: Optional[int] = None, count: int = 8,
        thumb: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        """Disassemble *count* instructions starting at *addr* (defaults to
        the current PC). Returns a list of {addr, size, bytes, mnemonic,
        op_str, text} dicts. *thumb* overrides Thumb/ARM mode selection on
        ARM (defaults to Thumb for cortex-m3, ARM otherwise)."""
        if count <= 0 or count > 256:
            raise SessionError("count must be in (0, 256]")
        self.assert_active()
        self.assert_idle()
        md, is_thumb = self._capstone(thumb)
        with self._lock:
            if addr is None:
                addr = self.backend.read_register("pc")
            if is_thumb:
                addr &= ~1
            # Over-read so the final instruction isn't truncated. RISC targets
            # are <=4 bytes/insn but x86 runs up to 15, so budget 16 per insn;
            # capstone stops after *count* anyway, so the slack is harmless.
            nbytes = min(count * 16 + 16, 0x10000)
            code = bytes(self.backend.read_memory(addr, 1, nbytes, raw=True))
        out: List[Dict[str, Any]] = []
        for insn in md.disasm(code, addr, count):
            out.append({
                "addr": insn.address,
                "size": insn.size,
                "bytes": insn.bytes.hex(),
                "mnemonic": insn.mnemonic,
                "op_str": insn.op_str,
                "text": f"{insn.mnemonic} {insn.op_str}".strip(),
            })
        return out

    def _capstone(self, thumb: Optional[bool] = None) -> Tuple[Any, bool]:
        """Build a capstone disassembler matching the session's arch.
        Returns (Cs, is_thumb)."""
        try:
            import capstone as cs
        except ImportError as exc:  # pragma: no cover — install hint
            raise SessionError(
                "Disassembly needs capstone. Install it with: "
                "pip install 'capstone>=4.0' (or `pip install -e .[mcp]`)."
            ) from exc
        arch = self.config.machine.arch
        if arch in ("cortex-m3", "arm"):
            is_thumb = (arch == "cortex-m3") if thumb is None else thumb
            mode = cs.CS_MODE_THUMB if is_thumb else cs.CS_MODE_ARM
            return cs.Cs(cs.CS_ARCH_ARM, mode), is_thumb
        if arch == "arm64":
            arm64 = getattr(cs, "CS_ARCH_AARCH64", None) or cs.CS_ARCH_ARM64
            return cs.Cs(arm64, cs.CS_MODE_ARM), False
        if arch == "mips":
            return cs.Cs(
                cs.CS_ARCH_MIPS,
                cs.CS_MODE_MIPS32 | cs.CS_MODE_BIG_ENDIAN,
            ), False
        if arch in ("powerpc", "powerpc:MPC8XX"):
            return cs.Cs(
                cs.CS_ARCH_PPC, cs.CS_MODE_32 | cs.CS_MODE_BIG_ENDIAN,
            ), False
        if arch == "ppc64":
            return cs.Cs(
                cs.CS_ARCH_PPC, cs.CS_MODE_64 | cs.CS_MODE_BIG_ENDIAN,
            ), False
        if arch == "x86":
            return cs.Cs(cs.CS_ARCH_X86, cs.CS_MODE_32), False
        raise SessionError(f"No capstone mapping for arch {arch!r}")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _dispatch_loop(self, intercepts: Any) -> None:
        """Mirror main._in_process_dispatch_loop but record the result on
        self._last_run instead of returning. Runs in the worker thread."""
        from halucinator import hal_stats
        backend = self.backend
        backend.cont()
        while True:
            pc = backend.read_register("pc") & ~1
            bp_id = intercepts.addr2bp_lut.get(pc)
            if bp_id is None:
                # Stopped at an address we don't know — could be a debug
                # breakpoint set by the MCP client, or an exit.
                debug_bp = self.debug_breakpoints.get(pc)
                self._last_run = _RunResult(
                    pc=pc,
                    state="debug_bp" if debug_bp is not None else "stopped",
                    bp_id=debug_bp,
                )
                if debug_bp is None:
                    self._exited = True
                return
            info = intercepts.bp2handler_lut[bp_id]
            cls, method = info.cls, info.handler
            hal_stats.stats[bp_id]["count"] += 1
            try:
                do_intercept, ret_value = method(cls, backend, pc)
            except Exception as exc:  # noqa: BLE001
                self._last_run = _RunResult(
                    pc=pc, state="error", bp_id=bp_id,
                    error=f"bp_handler raised: {exc}",
                )
                return
            if do_intercept:
                backend.execute_return(ret_value)
            else:
                self._last_run = _RunResult(
                    pc=pc, state="hal_bp", bp_id=bp_id,
                    handler=hal_stats.stats[bp_id].get("function"),
                )
                return

    def _snapshot_run(self) -> Dict[str, Any]:
        # Don't read the backend PC while the worker thread is still alive
        # (a wedged/parked run after a timeout or stop()): that read races the
        # engine on another thread. Report pc=None in that case, mirroring
        # status(). Snapshot _last_run once so the reported fields are
        # internally consistent even if the worker reassigns it concurrently.
        worker_alive = self._worker is not None and self._worker.is_alive()
        pc: Optional[int] = None
        if not worker_alive:
            with self._lock:
                try:
                    pc = self.backend.read_register("pc")
                except Exception:  # noqa: BLE001
                    pc = -1
        lr = self._last_run
        return {
            "pc": pc,
            "state": lr.state,
            "bp_id": lr.bp_id,
            "handler": lr.handler,
            "error": lr.error,
            "running": self._running,
            "exited": self._exited,
        }
