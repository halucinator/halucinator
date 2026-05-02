"""
RenodeBackend — drives Antmicro's Renode as the emulation target via its
GDB stub (for register/memory/breakpoint access) and Monitor TCP socket
(for Renode-specific operations: machine setup, IRQ injection).

Usage notes:
  * Requires `renode` on PATH, or HALUCINATOR_RENODE pointing at the
    binary. Install via https://renode.io or `brew install renode` on
    macOS / apt on Debian.
  * Firmware + memory layout is generated as a .resc script on the fly.
  * For the existing multi_arch test firmware (no peripherals, just
    stubbed uart_init/write/read functions) we don't need any hardware
    models; a generic Renode machine with flat memory is enough.
"""
from __future__ import annotations

import logging
import os
import socket
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional, Tuple, Union

from .hal_backend import (
    ABI_MIXINS, ARM32HalMixin, HalBackend, MemoryRegion,
)
from .qemu_backend import _GDBClient

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Renode Monitor TCP client
# ---------------------------------------------------------------------------

class _MonitorClient:
    """Minimal Renode Monitor TCP client. Sends one-line commands terminated
    by \\n and reads responses until the next prompt token."""

    PROMPT = b"(monitor) "

    def __init__(self, host: str = "localhost", port: int = 1234,
                 timeout: float = 5.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: Optional[socket.socket] = None
        self._lock = threading.Lock()

    def connect(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.settimeout(self.timeout)
        self._sock.connect((self.host, self.port))
        # Drain the Telnet IAC negotiation + banner. Renode's Monitor
        # opens in Telnet mode and starts with IAC bytes; we don't
        # actually respond to IAC but the prompt eventually arrives.
        self._drain(0.5)

    def disconnect(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def execute(self, command: str) -> bytes:
        """Send *command* and drain the reply. Returns best-effort bytes."""
        with self._lock:
            self._sock.sendall(command.encode() + b"\r\n")
            return self._drain(0.5)

    def _drain(self, max_wait: float) -> bytes:
        """Read whatever's available for up to *max_wait* seconds."""
        buf = b""
        prev = self._sock.gettimeout()
        self._sock.settimeout(max_wait)
        try:
            while True:
                chunk = self._sock.recv(4096)
                if not chunk:
                    return buf
                buf += chunk
        except (socket.timeout, BlockingIOError):
            return buf
        finally:
            self._sock.settimeout(prev)

    # Back-compat alias for tests
    def _read_until_prompt(self) -> bytes:
        return self._drain(0.5)


# ---------------------------------------------------------------------------
# RenodeBackend
# ---------------------------------------------------------------------------

# Renode machine descriptions per arch. "cpu_type" maps to the Renode
# string used in `mach create "<arch>"` and the `cpu.<name>` Monitor path
# for register access. For the multi_arch test firmware we don't need
# hardware peripherals — the bp_handlers stub everything — so a minimal
# machine with flat memory suffices.
_ARCH_MAP: Dict[str, Dict[str, str]] = {
    "cortex-m3":      {"cpu_type": "CortexM",    "arch_key": "arm"},
    "arm":            {"cpu_type": "ARMv7A",     "arch_key": "arm"},
    "arm64":          {"cpu_type": "ARMv8A",     "arch_key": "arm64"},
    # mips omitted — CPU.MIPS* doesn't resolve on linux-arm64-dotnet-portable
    "powerpc":        {"cpu_type": "PowerPc",    "arch_key": "powerpc"},
    "powerpc:MPC8XX": {"cpu_type": "PowerPc",    "arch_key": "powerpc"},
    "ppc64":          {"cpu_type": "PowerPc64",  "arch_key": "ppc64"},
}


class RenodeBackend(ARM32HalMixin, HalBackend):
    """
    HalBackend backed by Antmicro Renode.

    Talks to Renode over two sockets:
      * GDB stub (opened via Monitor `machine StartGdbServer`) — reuses the
        same _GDBClient as QEMUBackend for register/memory/breakpoints.
      * Monitor TCP — used for machine setup and IRQ injection.
    """

    def __init__(
        self,
        config: Any = None,
        arch: str = "cortex-m3",
        renode_path: Optional[str] = None,
        gdb_host: str = "localhost",
        gdb_port: int = 3333,
        monitor_host: str = "localhost",
        monitor_port: int = 1234,
        **kwargs: Any,
    ):
        self.config = config
        self.arch = arch
        self.renode_path = renode_path or os.environ.get(
            "HALUCINATOR_RENODE", "renode"
        )
        self._gdb = _GDBClient(gdb_host, gdb_port, arch=arch)
        self._monitor = _MonitorClient(monitor_host, monitor_port)
        self._process: Optional[subprocess.Popen] = None
        self._bp_map: Dict[int, int] = {}
        self._next_bp_id = 1
        self._regions: List[MemoryRegion] = []
        self._script_path: Optional[str] = None
        # Optional initial PC/SP stamped into the .resc so Renode's CPU
        # starts at the right address — halucinator's GDB register writes
        # don't always propagate to Renode's CPU state on cortex-m.
        self._initial_pc: Optional[int] = None
        self._initial_sp: Optional[int] = None
        self._machine_started: bool = False

        # Arch-specific ABI binding (same pattern as QEMUBackend).
        abi_cls = ABI_MIXINS.get(arch, ARM32HalMixin)
        self._abi = abi_cls
        if abi_cls is not ARM32HalMixin:
            for method_name in ("get_arg", "set_args", "get_ret_addr",
                                "set_ret_addr", "execute_return",
                                "read_string"):
                method = getattr(abi_cls, method_name, None)
                if method is not None:
                    setattr(self, method_name,
                            method.__get__(self, type(self)))

    def set_initial_state(self, pc: Optional[int] = None,
                          sp: Optional[int] = None) -> None:
        """Stash PC/SP to be written into the .resc script (prior to
        StartGdbServer) so Renode's Monitor-side CPU state agrees with
        what halucinator expects. Called by _emulate_with_renode_backend
        after parsing the YAML config."""
        if pc is not None:
            self._initial_pc = pc
        if sp is not None:
            self._initial_sp = sp

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def add_memory_region(self, region: MemoryRegion) -> None:
        self._regions.append(region)

    def launch(self, script_dir: str) -> None:
        """Generate a .resc script, spawn Renode, and connect GDB.

        The .resc creates the machine, loads firmware, starts the GDB
        server, and halts at reset (`start` is only issued once GDB is
        connected and halucinator has set PC/SP). We keep Monitor
        interaction optional — Renode's Monitor uses Telnet framing
        (IAC negotiation, echo, etc.) which is fiddly; the GDB stub
        gives us everything we need for register/memory/breakpoints.
        """
        os.makedirs(script_dir, exist_ok=True)
        self._script_path = os.path.join(script_dir, "halucinator.resc")
        self._write_resc_script(self._script_path)

        log_path = os.path.join(script_dir, "renode.log")
        # -P port opens the Monitor on TCP so we can send `start` after
        # halucinator has registered its breakpoints.
        cmd = [self.renode_path, "--plain", "--disable-xwt",
               "-P", str(self._monitor.port),
               "-e", f"include @{self._script_path}"]
        log.info("Launching Renode: %s", " ".join(cmd))
        self._process = subprocess.Popen(
            cmd,
            stdout=open(log_path, "wb"),
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )

        retries = 20
        last_err: Optional[Exception] = None
        for i in range(retries):
            try:
                self._gdb.connect()
                last_err = None
                break
            except (ConnectionRefusedError, OSError) as exc:
                last_err = exc
                time.sleep(0.5)
        if last_err is not None:
            raise last_err

        # Open Monitor alongside so cont() can un-pause the machine.
        for i in range(retries):
            try:
                self._monitor.connect()
                break
            except ConnectionRefusedError:
                time.sleep(0.3)

        self._machine_started = False

    # Renode CPU class name + the cpuType string it expects per halucinator
    # arch. The CPU class goes into `CPU.<Class>` in the .repl/.resc; the
    # cpuType is the concrete sub-variant.
    # NOTE: MIPS is intentionally absent — the linux-arm64-dotnet-portable
    # Renode release doesn't ship a MIPS CPU class (CPU.MIPS, MIPSCpu,
    # MIPS4Kc, etc. all fail to resolve at LoadPlatformDescription time).
    # Use --emulator avatar2 / qemu / unicorn for mips firmware.
    _CPU_TYPE: Dict[str, Tuple[str, str]] = {
        "cortex-m3":      ("CortexM",   "cortex-m3"),
        "arm":            ("ARMv7A",    "cortex-a7"),
        "arm64":          ("ARMv8A",    "cortex-a53"),
        "powerpc":        ("PowerPc",   "e200z6"),
        "powerpc:MPC8XX": ("PowerPc",   "e200z6"),
        "ppc64":          ("PowerPc64", "power8"),
    }

    def _repl_cpu_block(self, cpu_class: str, cpu_type: str) -> List[str]:
        """Per-arch platform description for the CPU (and any mandatory
        supporting peripherals like cortex-m's NVIC or ARMv8A's GIC)."""
        lines: List[str] = []
        if cpu_class == "CortexM":
            lines += [
                "nvic: IRQControllers.NVIC @ sysbus 0xE000E000",
                "    priorityMask: 0xF0",
                "    systickFrequency: 16000000",
                "    IRQ -> cpu@0",
                "",
                "cpu: CPU.CortexM @ sysbus",
                f"    cpuType: \"{cpu_type}\"",
                "    nvic: nvic",
                "",
            ]
        elif cpu_class == "ARMv8A":
            # ARMv8A requires a GIC attached to the CPU.
            lines += [
                "cpu: CPU.ARMv8A @ sysbus",
                f"    cpuType: \"{cpu_type}\"",
                "    genericInterruptController: gic",
                "",
                "gic: IRQControllers.ARM_GenericInterruptController @ {",
                "        sysbus new Bus.BusMultiRegistration { "
                "address: 0x8000000; size: 0x010000; region: \"distributor\" };",
                "        sysbus new Bus.BusMultiRegistration { "
                "address: 0x8010000; size: 0x010000; region: \"cpuInterface\" }",
                "    }",
                "    architectureVersion: .GICv2",
                "    supportsTwoSecurityStates: true",
                "",
            ]
        elif cpu_class in ("PowerPc", "PowerPc64"):
            # PPC platforms need the sysbus endianness set before the CPU.
            # PowerPc64 in Renode doesn't take a cpuType param (it fixes
            # the arch to 64-bit power); 32-bit PowerPc does.
            lines += [
                "sysbus:",
                "    Endianess: Endianess.BigEndian",
                "",
                f"cpu: CPU.{cpu_class} @ sysbus",
            ]
            if cpu_class == "PowerPc":
                lines.append(f"    cpuType: \"{cpu_type}\"")
            lines.append("")
        else:
            lines += [
                f"cpu: CPU.{cpu_class} @ sysbus",
                f"    cpuType: \"{cpu_type}\"",
                "",
            ]
        return lines

    def _write_resc_script(self, path: str) -> None:
        """Write a .resc + accompanying .repl describing the platform.

        Renode's Monitor is finicky about quoting inside
        LoadPlatformDescriptionFromString when invoked from command-line
        `-e` or from include scripts, so we write the full platform to a
        sibling .repl file and load that. That's also the canonical
        Renode pattern in official board descriptions.
        """
        info = self._CPU_TYPE.get(self.arch)
        if info is None:
            raise ValueError(
                f"Unsupported arch for RenodeBackend: {self.arch!r}"
            )
        cpu_class, cpu_type = info
        script_dir = os.path.dirname(path) or "."
        repl_path = os.path.join(script_dir, "halucinator.repl")

        # .repl — Renode platform description (indented attributes).
        repl_lines = self._repl_cpu_block(cpu_class, cpu_type)
        # Map a small "lowmem" region at 0x0 so early firmware reads
        # from absolute addresses < 0x1000 silently return zero, matching
        # QEMU/avatar-qemu's implicit behavior. Skip if the user has
        # already mapped anything overlapping 0x0.
        if not any(r.base_addr == 0 for r in self._regions):
            repl_lines.append("lowmem: Memory.MappedMemory @ sysbus 0x0")
            repl_lines.append("    size: 0x1000")
            repl_lines.append("")
        for region in self._regions:
            repl_lines.append(
                f"mem{region.base_addr:x}: Memory.MappedMemory @ sysbus "
                f"{hex(region.base_addr)}"
            )
            repl_lines.append(f"    size: {hex(region.size)}")
            repl_lines.append("")
        # Append any MMIO Python-peripheral entries the caller staged
        # via _extra_repl_lines (set by main._renode_mmio_setup).
        extra = getattr(self, "_extra_repl_lines", None)
        if extra:
            repl_lines.extend(extra)
        with open(repl_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(repl_lines))

        # .resc — load the platform, load firmware bytes, start GDB stub.
        lines = [
            "# Auto-generated by halucinator RenodeBackend — do not edit",
            "mach create \"halucinator\"",
            f"machine LoadPlatformDescription @{os.path.abspath(repl_path)}",
        ]
        firmware_base: Optional[int] = None
        for region in self._regions:
            if region.file:
                abs_path = os.path.abspath(region.file)
                lines.append(
                    f"sysbus LoadBinary @{abs_path} {hex(region.base_addr)}"
                )
                if firmware_base is None:
                    firmware_base = region.base_addr
        # For cortex-m, set VTOR to the firmware base so NVIC exception
        # vectors resolve correctly later. We deliberately do NOT `cpu
        # Reset` — halucinator's test firmwares don't have a cortex-m
        # vector table at offset 0 of flash (just .text from entry_addr
        # onward), so a reset would read garbage SP/PC and halt.
        if cpu_class == "CortexM" and firmware_base is not None:
            lines.append(f"cpu VectorTableOffset {hex(firmware_base)}")
        # Stamp initial PC/SP via the Monitor — Renode's GDB stub doesn't
        # always propagate register writes to CPU state on cortex-m, but
        # the Monitor's CPU setters do (and handle the Thumb bit correctly
        # on PC writes for cortex-m). PowerPc uses r1 as the stack
        # register and exposes it only through SetRegisterUlong, not the
        # named `SP` property.
        if self._initial_sp is not None:
            if cpu_class in ("PowerPc", "PowerPc64"):
                lines.append(
                    f"cpu SetRegisterUlong \"r1\" {hex(self._initial_sp)}"
                )
            else:
                lines.append(f"cpu SP {hex(self._initial_sp)}")
        if self._initial_pc is not None:
            lines.append(f"cpu PC {hex(self._initial_pc)}")
        # Start GDB stub with autostart=False: machine stays paused so
        # halucinator can register breakpoints before execution starts.
        # The first cont() sends `start` via the Monitor to un-pause the
        # machine.
        lines.append(f"machine StartGdbServer {self._gdb.port} False")
        lines.append("")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))

    def shutdown(self) -> None:
        try:
            self._gdb.disconnect()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._monitor._sock:  # pylint: disable=protected-access
                self._monitor.execute("quit")
        except Exception:  # noqa: BLE001
            pass
        self._monitor.disconnect()
        if self._process:
            self._process.terminate()
            self._process = None

    # ------------------------------------------------------------------
    # HalBackend primitives — delegate to GDB client, same as QEMUBackend
    # ------------------------------------------------------------------

    def read_memory(self, addr: int, size: int, num_words: int = 1,
                    raw: bool = False) -> Union[int, bytes]:
        total = size * num_words
        data = self._gdb.read_memory(addr, total)
        if raw or num_words > 1:
            return bytes(data)
        order = "big" if self._gdb._big_endian_arch() else "little"  # pylint: disable=protected-access
        return int.from_bytes(data[:size], order)

    def write_memory(self, addr: int, size: int,
                     value: Union[int, bytes, bytearray],
                     num_words: int = 1, raw: bool = False) -> bool:
        if isinstance(value, (bytes, bytearray)):
            data = bytes(value)
        else:
            order = "big" if self._gdb._big_endian_arch() else "little"  # pylint: disable=protected-access
            data = value.to_bytes(size * num_words, order)
        try:
            self._gdb.write_memory(addr, data)
            return True
        except OSError:
            return False

    def read_register(self, register: str) -> int:
        return self._gdb.read_register(register)

    # Renode's cortex-m GDB stub accepts 'G' packet register writes and
    # reports them back on read, but doesn't actually propagate the new
    # value to the CPU core — so a GDB-only execute_return (pc=lr) ends
    # up wandering into garbage once the CPU resumes. The Monitor's
    # `cpu <REG> <VAL>` setter hits the real CPU state, so we route the
    # architectural registers that bp_handlers write most often through
    # the Monitor for cortex-m.
    _MONITOR_REGS_CORTEX_M = {"pc", "sp", "lr", "r0", "r1", "r2", "r3",
                              "r4", "r5", "r6", "r7", "r8", "r9",
                              "r10", "r11", "r12"}

    def write_register(self, register: str, value: int) -> None:
        key = register.lower()
        if (self.arch == "cortex-m3"
                and key in self._MONITOR_REGS_CORTEX_M
                and self._monitor._sock is not None):  # pylint: disable=protected-access
            try:
                self._monitor.execute(
                    f"cpu {key.upper() if key in ('pc', 'sp', 'lr') else key} "
                    f"{value:#x}"
                )
                return
            except Exception as exc:  # noqa: BLE001
                log.warning("Monitor %s write failed (%s); falling back to GDB",
                            key, exc)
        self._gdb.write_register(register, value)

    def set_breakpoint(self, addr: int, hardware: bool = False,
                       temporary: bool = False) -> int:
        self._gdb.set_breakpoint(addr)
        bp_id = self._next_bp_id
        self._next_bp_id += 1
        self._bp_map[bp_id] = addr
        return bp_id

    def remove_breakpoint(self, bp_id: int) -> None:
        addr = self._bp_map.pop(bp_id, None)
        if addr is not None:
            self._gdb.remove_breakpoint(addr)

    def set_watchpoint(self, addr: int, write: bool = True,
                       read: bool = False, size: int = 4) -> int:
        self._gdb.set_watchpoint(addr, size=size, read=read, write=write)
        bp_id = self._next_bp_id
        self._next_bp_id += 1
        self._bp_map[bp_id] = (addr, size, read, write)
        return bp_id

    def remove_watchpoint(self, bp_id: int) -> None:
        entry = self._bp_map.pop(bp_id, None)
        if isinstance(entry, tuple) and len(entry) == 4:
            addr, size, read, write = entry
            self._gdb.remove_watchpoint(addr, size=size, read=read, write=write)

    def cont(self, blocking: bool = False) -> None:
        if not self._machine_started:
            # First cont: issue GDB 'c' so the stub queues a continue for
            # the current (still halted) CPU, THEN un-pause the machine
            # via Monitor 'start'. Order matters — 'c' without 'start' is
            # a no-op on a paused machine, and 'start' without a pending
            # 'c' makes Renode post a spurious initial-halt S05 that
            # desyncs the next GDB request/response pair.
            self._gdb.cont()
            try:
                self._monitor.execute("start")
            except Exception as exc:  # noqa: BLE001
                log.warning("Renode Monitor 'start' failed: %s", exc)
            self._machine_started = True
            if blocking:
                self._gdb.wait_for_stop()
            return
        # Subsequent conts: CPU was halted at a bp, resume via GDB.
        self._gdb.cont()
        if blocking:
            self._gdb.wait_for_stop()

    def stop(self) -> None:
        self._gdb.stop()

    def step(self) -> None:
        self._gdb.step()
        self._gdb.wait_for_stop(timeout=2.0)

    # ------------------------------------------------------------------
    # IRQ injection — Renode exposes per-CPU IRQ set via Monitor
    # ------------------------------------------------------------------

    def inject_irq(self, irq_num: int) -> None:
        # Renode's Monitor call depends on the CPU type; for a simple NVIC
        # on cortex-m, `sysbus.nvic OnGPIO <irq> True` triggers the IRQ.
        # Callers that need different wiring should override.
        try:
            self._monitor.execute(f"sysbus.cpu OnGPIO {irq_num} True")
        except Exception as exc:  # noqa: BLE001
            log.warning("inject_irq(%d): %s", irq_num, exc)
