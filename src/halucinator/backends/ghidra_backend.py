"""
GhidraBackend — in-process emulation via Ghidra's PCode EmulatorHelper.

Uses pyghidra (https://github.com/NationalSecurityAgency/ghidra/tree/master/Ghidra/Features/PyGhidra)
to embed the Ghidra JVM in Python and drive Ghidra's built-in emulator.

Unlike UnicornBackend which uses an ISA-specific TCG, Ghidra's emulator
runs PCode — Ghidra's IL — so it works on any processor Ghidra has a
language module for (ARM, ARM64, MIPS, PPC, RISC-V, AVR, 6502, …).
It's slower than Unicorn but has broader arch coverage and gives us
access to Ghidra's symbol resolution and decompiled structure.

Integration sketch:
  * `pyghidra.start()` boots a JVM with Ghidra on the classpath.
  * Memory regions + firmware bytes populate a transient Program built
    from the halucinator config.
  * `ghidra.app.emulator.EmulatorHelper` runs PCode with a step/cont
    API that we adapt to HalBackend.cont()/wait_for_stop() semantics.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Callable, Dict, List, Optional, Union

from .hal_backend import (
    ABI_MIXINS, ARM32HalMixin, HalBackend, MemoryRegion,
)
from .irq.in_process import InProcessIrqMixin

log = logging.getLogger(__name__)


try:
    import pyghidra  # noqa: F401 — deferred to init(); probed here for feature detection
    _HAVE_PYGHIDRA = True
except ImportError:
    _HAVE_PYGHIDRA = False


# Maps halucinator arch strings -> Ghidra language IDs. Ghidra uses
# "processor:endian:size:variant" (e.g. "ARM:LE:32:Cortex").
_LANGUAGE_MAP: Dict[str, str] = {
    "cortex-m3":      "ARM:LE:32:Cortex",
    "arm":            "ARM:LE:32:v7",
    "arm64":          "AARCH64:LE:64:v8A",
    "mips":           "MIPS:BE:32:default",
    "powerpc":        "PowerPC:BE:32:default",
    "powerpc:MPC8XX": "PowerPC:BE:32:MPC8270",
    "ppc64":          "PowerPC:BE:64:default",
    "x86":            "x86:LE:32:default",
}


class GhidraBackend(InProcessIrqMixin, ARM32HalMixin, HalBackend):
    """
    In-process emulation backend via Ghidra's PCode EmulatorHelper.
    """

    def __init__(
        self,
        config: Any = None,
        arch: str = "cortex-m3",
        ghidra_install_dir: Optional[str] = None,
        **kwargs: Any,
    ):
        if not _HAVE_PYGHIDRA:
            raise ImportError(
                "pyghidra is required for GhidraBackend. "
                "Install it with: pip install pyghidra"
            )
        self.config = config
        self.arch = arch
        self.ghidra_install_dir = (
            ghidra_install_dir
            or os.environ.get("GHIDRA_INSTALL_DIR")
        )
        self._regions: List[MemoryRegion] = []
        self._breakpoints: Dict[int, int] = {}  # addr -> bp_id
        # Watchpoints: bp_id -> (addr, size, read, write)
        self._watchpoints: Dict[int, tuple] = {}
        self._next_bp_id = 1

        # Ghidra / PCode state — populated in init()
        self._emulator: Optional[Any] = None
        self._program: Optional[Any] = None
        self._address_factory: Optional[Any] = None
        self._language: Optional[Any] = None
        self._stopped = True
        self._bp_hit_addr: Optional[int] = None
        # IRQ queue populated from another thread (peripheral_server's
        # zmq handler). cont() drains the queue between emulator.run
        # chunks so the synthetic exception frame setup is
        # single-threaded — Ghidra's setContextRegister can only run
        # while the emulator is in STOPPED state.
        # In-process IRQ state (queue + HAL_DET_TICK) — see InProcessIrqMixin.
        self._init_in_process_irq()

        # Arch-specific ABI binding.
        self._bind_abi(arch)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def add_memory_region(self, region: MemoryRegion) -> None:
        self._regions.append(region)

    def init(self) -> None:
        """Start the JVM, build a transient Program for the firmware, and
        create an EmulatorHelper ready to run."""
        import pyghidra as _pyghidra
        if not _pyghidra.started():
            kwargs = {}
            if self.ghidra_install_dir:
                kwargs["install_dir"] = self.ghidra_install_dir
            launcher = _pyghidra.HeadlessPyGhidraLauncher(**kwargs)
            launcher.add_vmargs("-Xmx4g")
            launcher.start()

        # Grab the Java classes we need
        from ghidra.program.model.lang import LanguageID
        from ghidra.program.util import DefaultLanguageService
        from ghidra.program.database import ProgramDB
        from ghidra.app.emulator import EmulatorHelper

        lang_id_str = _LANGUAGE_MAP.get(self.arch)
        if lang_id_str is None:
            raise ValueError(
                f"GhidraBackend: no language mapping for arch={self.arch!r}"
            )
        language_service = DefaultLanguageService.getLanguageService()
        self._language = language_service.getLanguage(LanguageID(lang_id_str))

        # Build a transient Program: no file, just memory blocks populated
        # with firmware bytes. ProgramDB requires a non-null consumer —
        # any object will do; it's used only for the reference-count
        # tracking we hit on release().
        from java.lang import Object as _JObject  # type: ignore
        compiler_spec = self._language.getDefaultCompilerSpec()
        self._consumer = _JObject()
        self._program = ProgramDB(
            "halucinator",
            self._language,
            compiler_spec,
            self._consumer,
        )
        self._program.startTransaction("init")
        memory = self._program.getMemory()
        self._address_factory = self._program.getAddressFactory()
        default_space = self._address_factory.getDefaultAddressSpace()

        from ghidra.program.model.mem import MemoryConflictException  # type: ignore
        for region in self._regions:
            start = default_space.getAddress(region.base_addr)
            try:
                if region.file and os.path.isfile(region.file):
                    with open(region.file, "rb") as fh:
                        data = fh.read(region.size)
                    if len(data) < region.size:
                        data = data + b"\x00" * (region.size - len(data))
                    from java.io import ByteArrayInputStream  # type: ignore
                    stream = ByteArrayInputStream(bytes(data))
                    block = memory.createInitializedBlock(
                        region.name, start, stream, region.size, None, False,
                    )
                else:
                    block = memory.createUninitializedBlock(
                        region.name, start, region.size, False,
                    )
            except MemoryConflictException as e:
                # Some firmware configs (e.g. multi_arch/mips) have
                # overlapping memory regions that avatar2/QEMU tolerate
                # silently. Ghidra rejects overlap, so skip the later one.
                log.warning(
                    "GhidraBackend: skipping region %s @ 0x%x (overlaps "
                    "existing memory): %s",
                    region.name, region.base_addr, e,
                )
                continue
            # All regions are read/write/execute for the PCode emulator —
            # halucinator doesn't model MPU permissions, and without
            # execute set the emulator faults as soon as PC enters code
            # that the default initialized block marked non-executable.
            block.setRead(True)
            block.setWrite(True)
            block.setExecute(True)

        self._emulator = EmulatorHelper(self._program)

        if self.arch in ("cortex-m3", "arm"):
            self._patch_arm_setISAMode()
            self._patch_arm_unimplemented_callothers()
        elif self.arch == "arm64":
            self._patch_arm_unimplemented_callothers()

    def shutdown(self) -> None:
        if self._emulator is not None:
            try:
                self._emulator.dispose()
            except Exception:  # noqa: BLE001
                pass
            self._emulator = None
        if self._program is not None:
            try:
                self._program.release(self._consumer)
            except Exception:  # noqa: BLE001
                pass
            self._program = None

    # ------------------------------------------------------------------
    # HalBackend primitives
    # ------------------------------------------------------------------

    def _addr(self, addr: int):
        default_space = self._address_factory.getDefaultAddressSpace()
        return default_space.getAddress(addr)

    # Ghidra register-name lookup fails for some cross-arch aliases
    # (e.g. "sp" on PowerPC where the stack pointer is r1). Normalize
    # common names so callers can keep using the shared HalBackend
    # vocabulary without knowing which register file a given arch has.
    _REGISTER_ALIASES: Dict[str, Dict[str, str]] = {
        "powerpc":        {"sp": "r1", "lr": "LR", "ctr": "CTR",
                           "xer": "XER", "msr": "MSR", "cr": "CR"},
        "powerpc:MPC8XX": {"sp": "r1", "lr": "LR", "ctr": "CTR",
                           "xer": "XER", "msr": "MSR", "cr": "CR"},
        "ppc64":          {"sp": "r1", "lr": "LR", "ctr": "CTR",
                           "xer": "XER", "msr": "MSR", "cr": "CR"},
        "mips":           {"sp": "sp"},   # MIPS has "sp" directly
    }

    def _resolve_register(self, name: str):
        """Ghidra-side register lookup with cross-arch name aliases."""
        alias = self._REGISTER_ALIASES.get(self.arch, {}).get(name)
        candidates = (alias, name) if alias else (name,)
        for cand in candidates:
            reg = self._language.getRegister(cand)
            if reg is not None:
                return reg
        return None

    def read_memory(self, addr: int, size: int, num_words: int = 1,
                    raw: bool = False) -> Union[int, bytes]:
        total = size * num_words
        data = bytes(self._emulator.readMemory(self._addr(addr), total))
        if raw or num_words > 1:
            return data
        endian = "big" if self._language.isBigEndian() else "little"
        return int.from_bytes(data[:size], endian)

    def write_memory(self, addr: int, size: int,
                     value: Union[int, bytes, bytearray],
                     num_words: int = 1, raw: bool = False) -> bool:
        if isinstance(value, (bytes, bytearray)):
            data = bytes(value)
        else:
            endian = "big" if self._language.isBigEndian() else "little"
            data = value.to_bytes(size * num_words, endian)
        try:
            self._emulator.writeMemory(self._addr(addr), data)
            return True
        except Exception:  # noqa: BLE001
            return False

    def read_register(self, register: str) -> int:
        reg = self._resolve_register(register)
        if reg is None:
            raise ValueError(f"Unknown register: {register!r}")
        return int(self._emulator.readRegister(reg).longValue())

    def write_register(self, register: str, value: int) -> None:
        reg = self._resolve_register(register)
        if reg is None:
            raise ValueError(f"Unknown register: {register!r}")
        from java.math import BigInteger  # type: ignore
        value = int(value)
        # ARM/Cortex-M Thumb bit convention: PC values with bit0 set
        # mean "this code is Thumb". Ghidra represents Thumb via the
        # TMode context register (not bit0 of PC), so split the two
        # here and propagate TMode when PC gets a Thumb-tagged value.
        if register.lower() == "pc" and self.arch in ("cortex-m3", "arm"):
            tmode = self._language.getRegister("TMode")
            if tmode is not None:
                from ghidra.program.model.lang import RegisterValue  # type: ignore
                self._emulator.setContextRegister(
                    RegisterValue(tmode, BigInteger.valueOf(value & 1))
                )
            value &= ~1
        self._emulator.writeRegister(reg, BigInteger.valueOf(value))

    def set_breakpoint(self, addr: int, hardware: bool = False,
                       temporary: bool = False) -> int:
        bp_id = self._next_bp_id
        self._next_bp_id += 1
        self._breakpoints[addr & ~1] = bp_id
        self._emulator.setBreakpoint(self._addr(addr))
        return bp_id

    def remove_breakpoint(self, bp_id: int) -> None:
        to_remove = [a for a, bid in self._breakpoints.items() if bid == bp_id]
        for addr in to_remove:
            try:
                self._emulator.clearBreakpoint(self._addr(addr))
            except Exception:  # noqa: BLE001
                pass
            del self._breakpoints[addr]

    def set_watchpoint(self, addr: int, write: bool = True,
                       read: bool = False, size: int = 4) -> int:
        """Install a write-watchpoint. Read watchpoints aren't supported —
        Ghidra's EmulatorHelper has no read-trap API, so `read=True` is
        silently ignored. The write side uses enableMemoryWriteTracking
        and step-mode in cont() to halt at the first covered write."""
        if not (write or read):
            raise ValueError("watchpoint must have read or write enabled")
        if read and not write:
            log.warning(
                "GhidraBackend: read-only watchpoints are not supported; "
                "use a different backend (qemu/unicorn) for read traps.",
            )
        bp_id = self._next_bp_id
        self._next_bp_id += 1
        self._watchpoints[bp_id] = (addr, size, read, write)
        return bp_id

    def remove_watchpoint(self, bp_id: int) -> None:
        self._watchpoints.pop(bp_id, None)

    def cont(self, blocking: bool = True) -> None:
        if self._emulator is None:
            raise RuntimeError("Call GhidraBackend.init() first")
        self._stopped = False
        from ghidra.util.task import TaskMonitor  # type: ignore
        # cortex-m3 firmwares can pend an IRQ from another thread,
        # which we must apply between instructions (Ghidra can't write
        # registers while the emulator is running). Watchpoints also
        # require single-stepping. In both cases we run in step-mode
        # and poll the queue every N instructions.
        irq_poll = (self.arch in ("cortex-m3", "arm", "arm64", "mips",
                                   "powerpc", "powerpc:MPC8XX", "ppc64"))
        if self._watchpoints or irq_poll:
            self._emulator.enableMemoryWriteTracking(bool(self._watchpoints))
            self._step_with_polling()
            return
        # Fast path: no IRQ source attached, no watchpoints. run() to
        # the next breakpoint or fault.
        hit = self._emulator.run(TaskMonitor.DUMMY)
        exec_addr = self._emulator.getExecutionAddress()
        pc = int(exec_addr.getUnsignedOffset()) if exec_addr is not None else 0
        self._bp_hit_addr = pc
        if hit:
            return
        if self._stopped:
            return
        state = str(self._emulator.getEmulateExecutionState())
        err = str(self._emulator.getLastError() or "")
        log.error(
            "GhidraBackend: cont() stopped at pc=0x%x state=%s%s",
            pc, state, f" err={err!r}" if err else "",
        )

    # Number of instructions to step between IRQ-queue polls. Larger
    # batches are faster; smaller batches make IRQ delivery latency
    # tighter. 256 is roughly the firmware-polling-loop length.
    _STEP_BATCH = 256

    def _step_with_polling(self) -> None:
        """Step the emulator instruction-by-instruction, draining the
        IRQ queue and checking breakpoints / watchpoints between
        chunks. Returns on a real breakpoint or watchpoint hit, on
        external stop(), or when the firmware enters an
        unrecoverable state."""
        from ghidra.util.task import TaskMonitor  # type: ignore
        while not self._stopped:
            # Apply any IRQs queued from another thread before
            # stepping further — write_register requires the emulator
            # to be in STOPPED state, which it is between step() calls.
            while self._pending_irqs:
                self._apply_pending_irq(self._pending_irqs.pop(0))
            for _ in range(self._STEP_BATCH):
                if not self._emulator.step(TaskMonitor.DUMMY):
                    # If the firmware just ran `bx lr` with LR holding
                    # an EXC_RETURN magic, Ghidra raises a decode
                    # FAULT at PC=0xFFFFFFFx. Pop the synthetic
                    # exception frame and resume.
                    exec_addr = self._emulator.getExecutionAddress()
                    pc = (int(exec_addr.getUnsignedOffset())
                          if exec_addr is not None else 0)
                    if self._maybe_handle_exc_return(pc):
                        # Clear the latched fault state and continue
                        # stepping at the restored PC.
                        try:
                            self._emulator.setHalt(False)
                        except Exception:  # noqa: BLE001
                            pass
                        break
                    state = str(self._emulator.getEmulateExecutionState())
                    err = str(self._emulator.getLastError() or "")
                    log.error(
                        "GhidraBackend: step() returned False "
                        "state=%s%s", state,
                        f" err={err!r}" if err else "",
                    )
                    return
                exec_addr = self._emulator.getExecutionAddress()
                pc = (int(exec_addr.getUnsignedOffset())
                      if exec_addr is not None else 0)
                if pc in self._breakpoints:
                    self._bp_hit_addr = pc
                    return
                if self._watchpoints and self._check_watchpoints():
                    return
                if self._pending_irqs:
                    break  # back to outer loop to drain

    def _check_watchpoints(self) -> bool:
        """Return True if any write-watchpoint fired since tracking was
        reset. Clears the tracked set for the next round."""
        tracked = self._emulator.getTrackedMemoryWriteSet()
        if tracked is None or tracked.isEmpty():
            return False
        for (addr, size, read, write) in self._watchpoints.values():
            if not write:
                continue
            start = self._addr(addr)
            end = self._addr(addr + size - 1)
            from ghidra.program.model.address import AddressRangeImpl  # type: ignore
            rng = AddressRangeImpl(start, end)
            if tracked.intersects(rng.getMinAddress(), rng.getMaxAddress()):
                self._bp_hit_addr = addr
                log.info(
                    "GhidraBackend: write-watchpoint hit at 0x%x (size %d)",
                    addr, size,
                )
                # Reset tracking so subsequent runs see fresh writes.
                self._emulator.getTrackedMemoryWriteSet().clear()
                return True
        return False

    def stop(self) -> None:
        self._stopped = True
        if self._emulator is not None:
            self._emulator.setHalt(True)

    def _request_break(self) -> None:
        """Thread-safe stop of the running emulator (InProcessIrqMixin
        primitive): ask run() to break so the dispatch thread drains the
        pending-IRQ queue."""
        if self._emulator is None:
            return
        try:
            self._emulator.setHalt(True)
        except Exception:  # noqa: BLE001
            pass

    def step(self) -> None:
        if self._emulator is None:
            raise RuntimeError("Call GhidraBackend.init() first")
        from ghidra.util.task import TaskMonitor  # type: ignore
        self._emulator.step(TaskMonitor.DUMMY)

    # ARM-v7M exception-return magic values. When an ISR does `bx lr` with
    # LR = one of these, the hardware normally pops the exception frame.
    # Ghidra's PCode emulator doesn't model that transition, so we mirror
    # the unicorn trick: catch the fetch at an EXC_RETURN address and
    # unwind the frame manually on the dispatch side.
    # EXC_RETURN constants + frame decode now live in InProcessIrqMixin.

    # Ghidra prefers the shadow-write path whenever the controller carries
    # shadow addresses (it sidesteps Sleigh banked-register quirks). See
    # InProcessIrqMixin._apply_pending_irq.
    _prefer_shadow_irq = True

    def set_vtor(self, vtor: int) -> None:
        """Remember the vector-table base so inject_irq can find ISRs."""
        self._vtor = vtor

    # ARMv7-A CPSR / mode constants.
    _ARM_MODE_IRQ = 0x12
    _ARM_MODE_MASK = 0x1F
    _ARM_CPSR_I = 0x80
    _ARM_CPSR_T = 0x20

    def _apply_pending_irq_armv7a(self, irq_num: int) -> None:
        """Synthesise an ARMv7-A IRQ entry. Mirror of UnicornBackend's
        version, but using Ghidra's writeRegister API.

        On entry sets:
          R14_irq  = PC + 4  (ARM-mode return correction)
          SPSR_irq = CPSR
          CPSR.M   = IRQ
          CPSR.I   = 1
          PC       = vbar + 0x18
        """
        if self._emulator is None:
            return
        cpsr = self.read_register("cpsr")
        if cpsr & self._ARM_CPSR_I:
            self._pending_irqs.insert(0, irq_num)
            try:
                self._emulator.setHalt(True)
            except Exception:  # noqa: BLE001
                pass
            return
        pc = self.read_register("pc")
        return_pc = pc + 4

        new_cpsr = cpsr & ~(self._ARM_MODE_MASK | self._ARM_CPSR_T)
        new_cpsr |= self._ARM_MODE_IRQ | self._ARM_CPSR_I

        # Ghidra ARM model: SP/LR/SPSR are banked per mode and Sleigh
        # exposes them as separate named registers. Switch CPSR mode
        # first so subsequent register writes target the right bank.
        from java.math import BigInteger  # type: ignore
        cpsr_reg = self._resolve_register("cpsr")
        self._emulator.writeRegister(cpsr_reg, BigInteger.valueOf(new_cpsr))

        # Banked LR_irq / SPSR_irq go by their explicit Sleigh names
        # in the ARM language. Fall back gracefully if the names
        # don't resolve on this build.
        for name, val in (("lr_irq", return_pc), ("spsr_irq", cpsr)):
            r = self._resolve_register(name)
            if r is not None:
                self._emulator.writeRegister(r, BigInteger.valueOf(int(val)))

        # GICC_IAR shadow write so the firmware ISR reads back the
        # acknowledged IRQ number on backends that don't model the
        # GIC CPU interface.
        ctrl = getattr(self, "_irq_controller", None)
        gicc_base = getattr(ctrl, "gicc_base", None) if ctrl else None
        if gicc_base is not None:
            try:
                self.write_memory(gicc_base + 0x0C, 1,
                                  int(irq_num).to_bytes(4, "little"),
                                  4, raw=True)
            except Exception:  # noqa: BLE001
                pass

        vbar = getattr(self, "_vtor", 0)
        # Plain writeRegister bypasses the TMode dance — we already
        # cleared T in CPSR.
        pc_reg = self._resolve_register("pc")
        self._emulator.writeRegister(pc_reg,
                                     BigInteger.valueOf(vbar + 0x18))
        log.info(
            "GhidraBackend.inject_irq(%d): ARMv7-A entry @ 0x%x, return=0x%x",
            irq_num, vbar + 0x18, return_pc,
        )

    def inject_irq(self, irq_num: int) -> None:
        """Deliver an external IRQ.

        Cortex-M3 fast-path: queue the IRQ for the dispatch thread
        and request the running emulator halt. cont() drains the
        queue (synthesises the exception frame, sets LR to
        EXC_RETURN, jumps PC to vector[16+N]) before re-running, so
        register writes happen while the emulator is in STOPPED
        state. Skips the controller-MMIO write — Ghidra's PCode
        emulator doesn't model the NVIC peripheral.

        Other arches fall through to HalBackend.inject_irq, which
        routes through the configured IrqController via memory or
        register writes; those go through the standard PCode emulator
        memory state and the firmware sees them on the next cont().
        """
        if self.arch not in ("cortex-m3", "arm", "arm64", "mips",
                              "powerpc", "powerpc:MPC8XX", "ppc64"):
            super().inject_irq(irq_num)
            return
        # On non-cortex-m archs, require an IrqController so the
        # error message points at the YAML the user needs to
        # declare. Check this before we touch _emulator so the
        # error is independent of init() ordering.
        if self.arch != "cortex-m3":
            ctrl = getattr(self, "_irq_controller", None)
            if ctrl is None:
                from halucinator.backends.irq import IrqConfigError
                raise IrqConfigError(
                    f"GhidraBackend(arch={self.arch!r}) has no "
                    "interrupt controller configured. Set "
                    "machine.interrupt_controller in the YAML.")
        if self._emulator is None:
            raise RuntimeError("Call GhidraBackend.init() first")
        # On non-cortex-m archs, also issue the IrqController MMIO
        # write so firmware that polls the controller registers
        # sees the bit set. The synthetic entry below transfers
        # control either way.
        if self.arch != "cortex-m3":
            try:
                ctrl.trigger(self, irq_num)
            except Exception as exc:  # noqa: BLE001
                # MIPS controller's RMW on CP0 'cause' may fail
                # under Ghidra; the synthetic shadow-write below
                # delivers anyway.
                if self.arch == "mips" and "cause" in str(exc):
                    pass
                else:
                    raise
        # Cross-thread safe: list.append is atomic in CPython.
        self._pending_irqs.append(int(irq_num))
        # setHalt asks the running emulator to break out of run().
        try:
            self._emulator.setHalt(True)
        except Exception:  # noqa: BLE001
            pass

    def _apply_cortex_m_fallback(self, irq_num: int) -> None:
        """Cortex-M (and un-migrated arch) fallback: push the 8-word
        exception frame and vector to vector[16+N]. Called by
        InProcessIrqMixin._apply_pending_irq (which handles the
        shadow-preference and arm/arm64/mips/ppc routing). Must run on the
        dispatch thread while the Ghidra emulator is STOPPED — writeRegister
        requires the emulator paused."""
        if self._emulator is None:
            return
        vtor = getattr(self, "_vtor", 0)
        isr_slot = vtor + (16 + irq_num) * 4
        try:
            isr_addr = self.read_memory(isr_slot, 4, 1)
        except Exception:  # noqa: BLE001
            isr_addr = 0
        if not isr_addr:
            log.warning(
                "GhidraBackend.inject_irq(%d): vector table slot 0x%x is "
                "zero or unmapped; no handler installed",
                irq_num, isr_slot,
            )
            return

        regs = {name: self.read_register(name) for name in
                ("r0", "r1", "r2", "r3", "r12", "lr", "pc", "cpsr")}
        sp = self.read_register("sp") - 32
        import struct
        frame = struct.pack(
            "<8I",
            regs["r0"], regs["r1"], regs["r2"], regs["r3"],
            regs["r12"], regs["lr"], regs["pc"], regs["cpsr"],
        )
        self.write_memory(sp, 1, frame, len(frame), raw=True)
        self.write_register("sp", sp)
        self.write_register("lr", self._EXC_RETURN_THREAD_MSP)
        # write_register("pc", isr_addr) handles Thumb bit via TMode.
        self.write_register("pc", isr_addr)
        log.info(
            "GhidraBackend.inject_irq(%d): entering ISR @ 0x%x (vector 0x%x)",
            irq_num, isr_addr, isr_slot,
        )

    def _apply_pending_irq_arm64(self, irq_num: int) -> None:
        """Synthesise an AArch64 IRQ entry. Mirrors the unicorn
        path: jump to ``irq_simple_entry`` (firmware-side AAPCS
        trampoline) with x30 = interrupted PC. The IrqController
        carries the trampoline address via ``irq_simple_entry``."""
        if self._emulator is None:
            return
        ctrl = getattr(self, "_irq_controller", None)
        irq_simple = (getattr(ctrl, "irq_simple_entry", None)
                      if ctrl else None)
        if irq_simple is None:
            log.warning("GhidraBackend.inject_irq(%d): arm64 controller "
                        "has no irq_simple_entry — IRQ won't deliver",
                        irq_num)
            return
        gicc_base = getattr(ctrl, "gicc_base", None) if ctrl else None
        if gicc_base is not None:
            try:
                self.write_memory(gicc_base + 0x0C, 1,
                                  int(irq_num).to_bytes(4, "little"),
                                  4, raw=True)
            except Exception:  # noqa: BLE001
                pass
        return_pc = self.read_register("pc")
        from java.math import BigInteger  # type: ignore
        for name, val in (("x30", return_pc), ("pc", int(irq_simple))):
            r = self._resolve_register(name)
            if r is not None:
                self._emulator.writeRegister(r, BigInteger.valueOf(int(val)))
        log.info(
            "GhidraBackend.inject_irq(%d): AArch64 trampoline @ 0x%x, "
            "return=0x%x", irq_num, irq_simple, return_pc,
        )

    def _maybe_handle_exc_return(self, pc: int) -> bool:
        """If PC now points at an EXC_RETURN magic value (as happens
        when an ISR does `bx lr`), pop the exception frame we pushed in
        inject_irq and resume pre-interrupt state. Returns True if
        handled."""
        decoded = self._decode_exc_return_frame(pc)
        if decoded is None:
            return False
        sp, frame = decoded
        # On exc_return Ghidra has already faulted on the
        # 0xFFFFFFFx fetch — the emulator is in FAULT state, which
        # rejects setContextRegister. Use the raw writeRegister path
        # for everything (TMode stays Thumb from before the ISR).
        from java.math import BigInteger  # type: ignore
        for name, val in (
            ("r0", frame[0]), ("r1", frame[1]), ("r2", frame[2]),
            ("r3", frame[3]), ("r12", frame[4]), ("lr", frame[5]),
            ("pc", frame[6] & ~1),
            ("cpsr", frame[7]), ("sp", sp + 32),
        ):
            reg = self._resolve_register(name)
            if reg is None:
                continue
            self._emulator.writeRegister(reg, BigInteger.valueOf(int(val)))
        log.info("GhidraBackend: exc_return — popped frame, resuming at 0x%x",
                 frame[6])
        return True

    # ------------------------------------------------------------------
    # ARM-specific: work around a Ghidra EmulatorHelper bug
    # ------------------------------------------------------------------

    def _patch_arm_setISAMode(self) -> None:
        """Replace ARM's built-in setISAMode pcode-op handler with a no-op.

        Ghidra's ARMEmulateInstructionStateModifier implements the
        setISAMode pcode-op (emitted by BL / BLX / BX for ARM↔Thumb
        switches) by calling Emulate.setContextRegisterValue(TMode).
        That method throws IllegalStateException unless the emulator is
        in STOPPED or BREAKPOINT state — but setISAMode fires *during*
        instruction execution (state=EXECUTE), so every BL / BLX crashes
        the PCode emulator into FAULT.

        Upstream Ghidra issue: the handler isn't safe to call mid-
        instruction. We track TMode manually in `write_register("pc",
        …)` by splitting the Thumb bit off the PC value and setting the
        context register ourselves, so the built-in handler is
        redundant. Swap in a no-op via reflection to unblock BL and
        BLX across the Cortex-M PCode emulator."""
        import jpype  # type: ignore
        from ghidra.pcode.emulate.callother import OpBehaviorOther  # type: ignore
        from java.lang import Integer as _JInteger  # type: ignore

        @jpype.JImplements(OpBehaviorOther)
        class _NoopSetISAMode:
            @jpype.JOverride
            def evaluate(self, emu, out, inputs):
                pass

        try:
            eh_cls = self._emulator.getClass()
            f1 = eh_cls.getDeclaredField("emulator"); f1.setAccessible(True)
            default_emu = f1.get(self._emulator)
            f2 = default_emu.getClass().getDeclaredField("emulator")
            f2.setAccessible(True)
            emulate = f2.get(default_emu)
            f3 = emulate.getClass().getDeclaredField("instructionStateModifier")
            f3.setAccessible(True)
            state_mod = f3.get(emulate)
            if state_mod is None:
                return   # nothing to patch
            parent = state_mod.getClass().getSuperclass()
            f_map = parent.getDeclaredField("pcodeOpMap")
            f_map.setAccessible(True)
            op_map = f_map.get(state_mod)
            # setISAMode is the 63rd user-op on ARM (index 62, 0x3e).
            for i in range(self._language.getNumberOfUserDefinedOpNames()):
                if str(self._language.getUserDefinedOpName(i)) == "setISAMode":
                    op_map.put(_JInteger(i), _NoopSetISAMode())
                    log.debug(
                        "GhidraBackend: swapped ARM setISAMode (op %d) "
                        "for a no-op to work around EmulatorHelper state "
                        "transition bug", i,
                    )
                    return
        except Exception as e:   # noqa: BLE001
            log.warning("GhidraBackend: setISAMode patch failed: %s", e)

    def _patch_arm_unimplemented_callothers(self) -> None:
        """Install no-op stubs for ARM CALLOTHER pcode-ops that Sleigh
        defines but the Cortex-M emulator doesn't implement, so kernel
        code (Zephyr, FreeRTOS) doesn't FAULT during boot.

        Each handler writes 0 to the output varnode if there is one (so
        the firmware sees a deterministic value); otherwise it's a true
        no-op. Returning 0 from `isCurrentModePrivileged` is fine because
        most kernel boot paths skip the privileged-only branch and fall
        into the unprivileged-but-functional branch — and either way
        we'd rather emulate forward than crash.
        """
        import jpype  # type: ignore
        from ghidra.pcode.emulate.callother import OpBehaviorOther  # type: ignore
        from java.lang import Integer as _JInteger  # type: ignore

        # Names from Ghidra/Sleigh ARM.sinc + ARMTHUMBinstructions.sinc.
        # We stub out:
        #  - mode/privilege query+set ops (kernel boot, exception entry/exit)
        #  - interrupt enable/disable ops (cps, msr PRIMASK)
        #  - main/process stack pointer accessors (Cortex-M dual-stack)
        #  - barrier/hint ops that have no architectural data effect
        #  - exclusive-access ops (ldrex/strex) — return 0 to indicate
        #    "no exclusive access lost" so the firmware proceeds
        #  - coprocessor accesses (Cortex-M MPU/SCB via CP15, FPU via CP10/11)
        # Crypto/SIMD/FP ops are deliberately *not* stubbed because the
        # firmware actually uses their results.
        explicit_targets = {
            "ClearExclusiveLocal", "ExclusiveAccess", "hasExclusiveAccess",
            "DataMemoryBarrier", "DataSynchronizationBarrier",
            "InstructionSynchronizationBarrier",
            "WaitForEvent", "WaitForInterrupt", "SendEvent",
            "HintDebug", "HintYield", "HintPreloadData",
            "HintPreloadDataForWrite", "HintPreloadInstruction",
            "isCurrentModePrivileged", "isThreadModePrivileged",
            "isThreadMode", "isUsingMainStack",
            "isFIQinterruptsEnabled", "isIRQinterruptsEnabled",
            "setCurrentModePrivileged", "setThreadModePrivileged",
            "setUserMode", "setAbortMode", "setFIQMode", "setIRQMode",
            "setStackMode", "setSupervisorMode", "setSystemMode",
            "setMonitorMode", "setUndefinedMode", "setEndianState",
            "enableIRQinterrupts", "disableIRQinterrupts",
            "enableFIQinterrupts", "disableFIQinterrupts",
            "enableDataAbortInterrupts", "disableDataAbortInterrupts",
            "getBasePriority", "setBasePriority",
            "getCurrentExceptionNumber",
            "getMainStackPointer", "setMainStackPointer",
            "getMainStackPointerLimit", "setMainStackPointerLimit",
            "getProcessStackPointer", "setProcessStackPointer",
            "getProcessStackPointerLimit", "setProcStackPointerLimit",
            "secureMonitorCall", "jazelle_branch",
            "software_bkpt", "software_hlt", "software_hvc",
            "software_smc", "software_interrupt", "software_udf",
            "DCPSInstruction", "IndexCheck", "SG", "TT", "TTA", "TTAT", "TTT",
        }
        prefix_targets = ("coproc_movefrom_", "coproc_moveto_",
                          "coprocessor_")

        @jpype.JImplements(OpBehaviorOther)
        class _ZeroReturning:
            @jpype.JOverride
            def evaluate(self, emu, out, inputs):
                if out is None:
                    return
                try:
                    state = emu.getMemoryState()
                    state.setValue(out, 0)
                except Exception:  # noqa: BLE001
                    pass

        try:
            eh_cls = self._emulator.getClass()
            f1 = eh_cls.getDeclaredField("emulator"); f1.setAccessible(True)
            default_emu = f1.get(self._emulator)
            f2 = default_emu.getClass().getDeclaredField("emulator")
            f2.setAccessible(True)
            emulate = f2.get(default_emu)
            f3 = emulate.getClass().getDeclaredField("instructionStateModifier")
            f3.setAccessible(True)
            state_mod = f3.get(emulate)
            if state_mod is None:
                return
            parent = state_mod.getClass().getSuperclass()
            f_map = parent.getDeclaredField("pcodeOpMap")
            f_map.setAccessible(True)
            op_map = f_map.get(state_mod)
            handler = _ZeroReturning()
            installed = []
            for i in range(self._language.getNumberOfUserDefinedOpNames()):
                name = str(self._language.getUserDefinedOpName(i))
                if name in explicit_targets or any(
                    name.startswith(p) for p in prefix_targets
                ):
                    op_map.put(_JInteger(i), handler)
                    installed.append(name)
            if installed:
                log.debug(
                    "GhidraBackend: installed zero-returning stubs for %d "
                    "ARM CALLOTHER pcodeops: %s", len(installed),
                    ", ".join(installed),
                )
        except Exception as e:  # noqa: BLE001
            log.warning(
                "GhidraBackend: ARM CALLOTHER stub install failed: %s", e,
            )
