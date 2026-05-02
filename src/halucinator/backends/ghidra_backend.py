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
}


class GhidraBackend(ARM32HalMixin, HalBackend):
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

        # Arch-specific ABI binding.
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
        if self._watchpoints:
            # Step-mode: run one instruction at a time and check the
            # tracked write set against every active write watchpoint.
            # Slower, but gives us halt-on-access semantics without a
            # dedicated pcode op hook — EmulatorHelper has no such API.
            self._emulator.enableMemoryWriteTracking(True)
            while not self._stopped:
                self._emulator.step(TaskMonitor.DUMMY)
                state = str(self._emulator.getEmulateExecutionState())
                if state != "STOPPED" and state != "BREAKPOINT":
                    break
                exec_addr = self._emulator.getExecutionAddress()
                pc = (int(exec_addr.getUnsignedOffset())
                      if exec_addr is not None else 0)
                if pc in self._breakpoints:
                    self._bp_hit_addr = pc
                    return
                if self._check_watchpoints():
                    return
            # fall through to error logging
            hit = False
        else:
            hit = self._emulator.run(TaskMonitor.DUMMY)
        exec_addr = self._emulator.getExecutionAddress()
        pc = int(exec_addr.getUnsignedOffset()) if exec_addr is not None else 0
        self._bp_hit_addr = pc
        if hit:
            return
        state = str(self._emulator.getEmulateExecutionState())
        err = str(self._emulator.getLastError() or "")
        log.error(
            "GhidraBackend: cont() stopped at pc=0x%x state=%s%s",
            pc, state, f" err={err!r}" if err else "",
        )

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
    _EXC_RETURN_THREAD_MSP = 0xFFFFFFF9
    _EXC_RETURN_MASK = 0xFFFFFFF0
    _EXC_RETURN_MAGIC = 0xFFFFFFF0

    def set_vtor(self, vtor: int) -> None:
        """Remember the vector-table base so inject_irq can find ISRs."""
        self._vtor = vtor

    def inject_irq(self, irq_num: int) -> None:
        """Deliver an external IRQ to a cortex-m CPU.

        Mirrors UnicornBackend.inject_irq: push an 8-word exception
        frame (r0–r3, r12, lr, pc, xpsr) onto the main stack, set LR to
        the thread-mode/MSP EXC_RETURN magic, and jump PC to the ISR
        address from the vector table.

        Ghidra's PCode emulator doesn't model Cortex-M exception
        delivery natively, so this is a software-synthesized transition
        that the ISR code sees as if it were hardware-driven."""
        if self.arch not in ("cortex-m3",):
            log.warning(
                "GhidraBackend.inject_irq(%d): only cortex-m3 has an "
                "in-process IRQ model; arch=%s is ignored",
                irq_num, self.arch,
            )
            return
        if self._emulator is None:
            raise RuntimeError("Call GhidraBackend.init() first")
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

    def _maybe_handle_exc_return(self, pc: int) -> bool:
        """If PC now points at an EXC_RETURN magic value (as happens
        when an ISR does `bx lr`), pop the exception frame we pushed in
        inject_irq and resume pre-interrupt state. Returns True if
        handled."""
        if self.arch != "cortex-m3":
            return False
        if (pc & self._EXC_RETURN_MASK) != self._EXC_RETURN_MAGIC:
            return False
        sp = self.read_register("sp")
        try:
            import struct
            raw = self.read_memory(sp, 1, 32, raw=True)
            frame = struct.unpack("<8I", bytes(raw))
        except Exception:  # noqa: BLE001
            return False
        self.write_register("r0", frame[0])
        self.write_register("r1", frame[1])
        self.write_register("r2", frame[2])
        self.write_register("r3", frame[3])
        self.write_register("r12", frame[4])
        self.write_register("lr", frame[5])
        self.write_register("pc", frame[6])
        self.write_register("cpsr", frame[7])
        self.write_register("sp", sp + 32)
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
