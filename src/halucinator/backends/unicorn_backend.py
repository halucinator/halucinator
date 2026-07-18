"""
UnicornBackend — in-process emulation via unicorn-engine.

No subprocess, no sockets: the firmware runs inside the Python process.
Breakpoints are implemented as unicorn CODE hooks.

Performance is typically 10-100× faster than the avatar2/QEMU path for
firmware that doesn't need real hardware peripheral timing.

Supported: ARM Thumb / ARM Cortex-M (primary target for halucinator).
           Other architectures can be added by extending the _ARCH_MAP.
"""
from __future__ import annotations

import logging
import struct
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from .hal_backend import ABI_MIXINS, ARM32HalMixin, ARMHalMixin, HalBackend, MemoryRegion

log = logging.getLogger(__name__)

try:
    import unicorn
    import unicorn.arm_const as arm_const
    try:
        import unicorn.arm64_const as arm64_const
    except ImportError:
        arm64_const = None  # type: ignore[assignment]
    try:
        import unicorn.mips_const as mips_const
    except ImportError:
        mips_const = None  # type: ignore[assignment]
    try:
        import unicorn.ppc_const as ppc_const
    except ImportError:
        ppc_const = None  # type: ignore[assignment]
    try:
        import unicorn.x86_const as x86_const
    except ImportError:
        x86_const = None  # type: ignore[assignment]
    _HAVE_UNICORN = True
except ImportError:
    _HAVE_UNICORN = False
    unicorn = None  # type: ignore[assignment]
    arm_const = None  # type: ignore[assignment]
    arm64_const = None  # type: ignore[assignment]
    mips_const = None  # type: ignore[assignment]
    ppc_const = None  # type: ignore[assignment]
    x86_const = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Architecture tables
# ---------------------------------------------------------------------------
#
# Maps halucinator arch string -> (unicorn_arch, unicorn_mode, is_thumb,
#   is_big_endian, word_size_bytes).
#
# Thumb applies only to 32-bit ARM; BE applies to MIPS and PPC. word_size
# controls pointer width in read_memory(..., num_words=1).
_ARCH_MAP: Dict[str, Tuple[str, str, bool, bool, int]] = {
    "cortex-m3":      ("arm",    "thumb", True,  False, 4),
    "arm":            ("arm",    "arm",   False, False, 4),
    "arm64":          ("arm64",  "arm",   False, False, 8),
    "mips":           ("mips",   "mips32_be", False, True, 4),
    "powerpc":        ("ppc",    "ppc32_be", False, True, 4),
    "powerpc:MPC8XX": ("ppc",    "ppc32_be", False, True, 4),
    "ppc64":          ("ppc",    "ppc64_be", False, True, 8),
    "x86":            ("x86",    "x86_32",   False, False, 4),
}

_PERM_MAP = {
    "r":   0x1,
    "w":   0x2,
    "x":   0x4,
    "rw":  0x3,
    "rx":  0x5,
    "rwx": 0x7,
    "xr":  0x5,
    "xrw": 0x7,
}

_REG_MAPS_CACHE: Dict[str, Dict[str, int]] = {}


def _get_arm_reg_map() -> Dict[str, int]:
    if "arm" in _REG_MAPS_CACHE:
        return _REG_MAPS_CACHE["arm"]
    if arm_const is None:
        return {}
    m = {
        **{f"r{i}": getattr(arm_const, f"UC_ARM_REG_R{i}") for i in range(13)},
        "sp":   arm_const.UC_ARM_REG_SP,
        "lr":   arm_const.UC_ARM_REG_LR,
        "pc":   arm_const.UC_ARM_REG_PC,
        "cpsr": arm_const.UC_ARM_REG_CPSR,
        "spsr": arm_const.UC_ARM_REG_SPSR,
    }
    _REG_MAPS_CACHE["arm"] = m
    return m


def _get_arm64_reg_map() -> Dict[str, int]:
    if "arm64" in _REG_MAPS_CACHE:
        return _REG_MAPS_CACHE["arm64"]
    if arm64_const is None:
        return {}
    m = {
        **{f"x{i}": getattr(arm64_const, f"UC_ARM64_REG_X{i}") for i in range(29)},
        "sp": arm64_const.UC_ARM64_REG_SP,
        "pc": arm64_const.UC_ARM64_REG_PC,
    }
    # x29 = fp, x30 = lr on AArch64
    for name, reg in (
        ("x29", "UC_ARM64_REG_X29"),
        ("x30", "UC_ARM64_REG_X30"),
        ("fp",  "UC_ARM64_REG_FP"),
        ("lr",  "UC_ARM64_REG_LR"),
    ):
        v = getattr(arm64_const, reg, None)
        if v is not None:
            m[name] = v
    _REG_MAPS_CACHE["arm64"] = m
    return m


def _get_mips_reg_map() -> Dict[str, int]:
    if "mips" in _REG_MAPS_CACHE:
        return _REG_MAPS_CACHE["mips"]
    if mips_const is None:
        return {}
    # Unicorn MIPS register consts: UC_MIPS_REG_0..UC_MIPS_REG_31 exist.
    # ABI aliases (a0-a3 = r4-r7, etc.) are named after registers in the
    # mips_const module too.
    m: Dict[str, int] = {}
    for i in range(32):
        m[f"r{i}"] = getattr(mips_const, f"UC_MIPS_REG_{i}")
    # ABI alias registers: these are named differently in mips_const
    aliases = {
        "zero": 0, "at": 1, "v0": 2, "v1": 3,
        "a0": 4, "a1": 5, "a2": 6, "a3": 7,
        "t0": 8, "t1": 9, "t2": 10, "t3": 11, "t4": 12,
        "t5": 13, "t6": 14, "t7": 15,
        "s0": 16, "s1": 17, "s2": 18, "s3": 19,
        "s4": 20, "s5": 21, "s6": 22, "s7": 23,
        "t8": 24, "t9": 25, "k0": 26, "k1": 27,
        "gp": 28, "sp": 29, "fp": 30, "ra": 31,
    }
    for name, idx in aliases.items():
        m[name] = getattr(mips_const, f"UC_MIPS_REG_{idx}")
    m["pc"] = mips_const.UC_MIPS_REG_PC
    _REG_MAPS_CACHE["mips"] = m
    return m


def _get_ppc_reg_map(word: int = 4) -> Dict[str, int]:
    cache_key = f"ppc{word * 8}"
    if cache_key in _REG_MAPS_CACHE:
        return _REG_MAPS_CACHE[cache_key]
    if ppc_const is None:
        return {}
    m: Dict[str, int] = {
        f"r{i}": getattr(ppc_const, f"UC_PPC_REG_{i}") for i in range(32)
    }
    # PPC SPRs that halucinator bp handlers commonly touch
    for name, const_name in (
        ("pc",  "UC_PPC_REG_PC"),
        ("msr", "UC_PPC_REG_MSR"),
        ("cr",  "UC_PPC_REG_CR"),
        ("lr",  "UC_PPC_REG_LR"),
        ("ctr", "UC_PPC_REG_CTR"),
        ("xer", "UC_PPC_REG_XER"),
    ):
        v = getattr(ppc_const, const_name, None)
        if v is not None:
            m[name] = v
    # r1 is the PPC stack pointer
    if "r1" in m:
        m["sp"] = m["r1"]
    _REG_MAPS_CACHE[cache_key] = m
    return m


def _get_x86_reg_map() -> Dict[str, int]:
    if "x86" in _REG_MAPS_CACHE:
        return _REG_MAPS_CACHE["x86"]
    if x86_const is None:
        return {}
    names = ("eax", "ebx", "ecx", "edx", "esi", "edi", "ebp", "esp",
             "eip", "eflags", "cs", "ds", "es", "fs", "gs", "ss")
    m: Dict[str, int] = {}
    for name in names:
        v = getattr(x86_const, f"UC_X86_REG_{name.upper()}", None)
        if v is not None:
            m[name] = v
    # halucinator's generic code (dispatch loop, regs.pc, MMIO pc capture)
    # uses the architecture-neutral names "pc" and "sp".
    if "eip" in m:
        m["pc"] = m["eip"]
    if "esp" in m:
        m["sp"] = m["esp"]
    _REG_MAPS_CACHE["x86"] = m
    return m


def _reg_map_for_arch(arch: str) -> Dict[str, int]:
    info = _ARCH_MAP.get(arch)
    if info is None:
        return _get_arm_reg_map()
    uc_arch = info[0]
    if uc_arch == "arm":
        return _get_arm_reg_map()
    if uc_arch == "arm64":
        return _get_arm64_reg_map()
    if uc_arch == "mips":
        return _get_mips_reg_map()
    if uc_arch == "ppc":
        word = info[4]
        return _get_ppc_reg_map(word)
    if uc_arch == "x86":
        return _get_x86_reg_map()
    return {}


# ---------------------------------------------------------------------------
# UnicornBackend
# ---------------------------------------------------------------------------

class UnicornBackend(ARMHalMixin, HalBackend):
    """
    In-process emulation backend using unicorn-engine.

    Usage::

        backend = UnicornBackend(arch="cortex-m3")
        backend.add_memory_region(MemoryRegion("flash", 0x08000000, 0x80000,
                                                permissions="rx",
                                                file="/path/to/firmware.bin"))
        backend.add_memory_region(MemoryRegion("ram", 0x20000000, 0x20000, "rw"))
        backend.init()

        bp_id = backend.set_breakpoint(0x08001234)
        backend.cont()            # runs until breakpoint
        pc = backend.read_register("pc")
    """

    def __init__(
        self,
        config: Any = None,
        arch: str = "cortex-m3",
        **kwargs: Any,
    ):
        if not _HAVE_UNICORN:
            raise ImportError(
                "unicorn-engine is required for UnicornBackend. "
                "Install it with: pip install unicorn"
            )
        self.config = config
        self.arch_name = arch
        self._uc: Optional[Any] = None           # unicorn.Uc instance
        self._regions: List[MemoryRegion] = []
        self._bp_hooks: Dict[int, Tuple[int, Any]] = {}  # bp_id → (addr, hook_h)
        self._mmio_hooks: Dict[int, Any] = {}    # region_name → hook_handle
        self._next_bp_id = 1
        self._stopped = True
        self._bp_hit_addr: Optional[int] = None
        # One-shot: when set, _code_hook lets execution pass this breakpoint
        # address ONCE without stopping. Used to step over a breakpoint after
        # an observe-only (non-intercept) bp_handler so the real function runs.
        self._skip_bp_once: Optional[int] = None
        self._breakpoints: Dict[int, int] = {}   # addr → bp_id
        # RAM-flag spin breaker (opt-in, see _code_hook / _break_ram_spin).
        # Detect a spin by DISTINCT-PC count over a window: a tight loop (even
        # one spanning a function call) touches few distinct PCs, while real
        # progress touches many. Catches call-based `while(check())` spins the
        # old contiguous-window detector missed.
        import os as _os
        self._break_ram_spins = _os.environ.get("HAL_BREAK_RAM_SPINS") == "1"
        self._spin_limit = int(_os.environ.get("HAL_RAM_SPIN_LIMIT", "200000"))
        self._spin_distinct_max = int(
            _os.environ.get("HAL_RAM_SPIN_DISTINCT", "48"))
        self._spin_pcs: set = set()
        self._spin_total = 0
        # Bad-call recovery (opt-in HAL_RECOVER_BAD_CALLS=1): on an invalid
        # instruction (a `mov pc, r2` indirect call through a `_func_` hook
        # bound to a routine we can't satisfy, landing in data), return to lr
        # — valid because the wrapper set it via `mov lr, pc`. Capped per
        # fault PC so a genuinely wedged address doesn't loop forever.
        self._recover_bad_calls = (
            _os.environ.get("HAL_RECOVER_BAD_CALLS") == "1")
        self._bad_call_recover: Dict[int, int] = {}
        # PC-write emulation (opt-in HAL_EMULATE_PC_WRITE=1): some firmware runs
        # downloaded native-ARM code (e.g. an M340 MAST program) whose `mov pc, Rm`
        # returns unicorn surfaces here as a spurious exception instead of just
        # branching. Emulate the write — set pc = Rm (the program's register, read
        # pre-exception-entry) — and continue. Uncapped (unlike _recover_bad_calls)
        # because a periodic scan revisits the same return sites every cycle.
        self._emulate_pc_write = (
            _os.environ.get("HAL_EMULATE_PC_WRITE") == "1")
        self._pc_write_emulated = 0
        # MMU flat-fallback (opt-in HAL_MMU_FLAT_FALLBACK=1): on an ARM data/
        # prefetch abort whose faulting address IS backed in physical memory
        # (uc.mem_read succeeds — i.e. the MMU translation failed but the page
        # is present), emulate the faulting load/store flat (VA==PA) and step
        # past it. Lets MMU-library code that walks page tables not yet mapped
        # in the active context proceed, where unicorn's CP15/TTBR handling
        # would otherwise data-abort-loop forever. See _mmu_flat_complete.
        self._mmu_flat_fallback = (
            _os.environ.get("HAL_MMU_FLAT_FALLBACK") == "1")
        self._mmu_flat_count: Dict[int, int] = {}
        self._bp_callbacks: Dict[int, Callable] = {}  # bp_id → callback
        # Pending IRQ injected from another thread (peripheral_server zmq
        # handler). cont() drains the queue before re-entering emu_start
        # so the synthetic exception frame is set up single-threaded.
        self._pending_irqs: List[int] = []
        # x86: when _intr_hook resolves a far control transfer (#GP from a
        # missing GDT), it stashes the resume EIP here so cont() re-enters
        # emu_start instead of aborting on the UcError. None when idle.
        self._x86_resume_eip: Optional[int] = None

        # Opt-in: skip an unhandled SVC instruction (advance past it and
        # zero r0) instead of aborting. Used to
        # tolerate fuzz-harness hypercalls baked into instrumented binaries
        # (e.g. P2IM's aflCall `svc #0x3f`).
        self.skip_svc: bool = False

        # Generic non-MMIO loop breaker (see _code_hook). Opt-in.
        self.auto_recover_loops: bool = False
        self._loop_lo: int = -1
        self._loop_count: int = 0
        self._loop_limit: int = 500_000
        self._loop_recover_budget: int = 200

        # Pre-compute the register name -> unicorn reg id map for this arch.
        self._reg_map = _reg_map_for_arch(arch)
        # Cache arch traits from _ARCH_MAP for hot paths (cont/read_memory).
        info = _ARCH_MAP.get(arch, ("arm", "thumb", True, False, 4))
        _, _, self._is_thumb, self._is_be, self._word_size = info

        # Bind the arch-specific ABI mixin onto the instance (ARM32 stays the
        # default via inheritance so existing arm/cortex-m callers are
        # unchanged).
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
    # Initialisation
    # ------------------------------------------------------------------

    def init(self) -> None:
        """Initialise unicorn engine and map all registered memory regions."""
        # Hoisted: `_os` is referenced unconditionally below (HAL_TRACK_READS,
        # HAL_SP_WATCH, HAL_PC_SAMPLE, HAL_CALL_TRACE, HAL_MAP_UNMAPPED). The
        # later `import os as _os` statements are kept as harmless re-imports
        # but this one is what Python's local-binding rule actually needs.
        import os as _os
        info = _ARCH_MAP.get(self.arch_name)
        if info is None:
            raise ValueError(
                f"Unsupported arch for UnicornBackend: {self.arch_name!r}"
            )
        arch_str, mode_str, _, _, _ = info

        if arch_str == "arm":
            uc_arch = unicorn.UC_ARCH_ARM
            uc_mode = (
                unicorn.UC_MODE_THUMB
                if mode_str == "thumb"
                else unicorn.UC_MODE_ARM
            )
        elif arch_str == "arm64":
            uc_arch = unicorn.UC_ARCH_ARM64
            uc_mode = unicorn.UC_MODE_ARM
        elif arch_str == "mips":
            uc_arch = unicorn.UC_ARCH_MIPS
            # MIPS32 big-endian is the default halucinator test firmware mode.
            uc_mode = unicorn.UC_MODE_MIPS32 | unicorn.UC_MODE_BIG_ENDIAN
        elif arch_str == "ppc":
            uc_arch = unicorn.UC_ARCH_PPC
            if mode_str.startswith("ppc64"):
                uc_mode = unicorn.UC_MODE_PPC64 | unicorn.UC_MODE_BIG_ENDIAN
            else:
                uc_mode = unicorn.UC_MODE_PPC32 | unicorn.UC_MODE_BIG_ENDIAN
        elif arch_str == "x86":
            uc_arch = unicorn.UC_ARCH_X86
            uc_mode = unicorn.UC_MODE_32
        else:
            raise ValueError(f"Unsupported arch for UnicornBackend: {arch_str!r}")

        self._uc = unicorn.Uc(uc_arch, uc_mode)
        log.info("Unicorn engine initialised: arch=%s mode=%s", arch_str, mode_str)

        # Cortex-M kernels (Zephyr, FreeRTOS, MCUXpresso) use `msr/mrs` to
        # special-purpose registers (PRIMASK, BASEPRI, FAULTMASK, CONTROL)
        # plus `wfi`/`wfe`/`sev`/`isb`/`dsb`/`dmb` during early boot. The
        # default unicorn ARM CPU is generic ARMv7-A which decodes Thumb-2
        # but not the M-profile system instructions — every PRIMASK write
        # raises UC_ERR_INSN_INVALID before the firmware finishes
        # initialisation. Pin the CPU model to Cortex-M3 so unicorn uses
        # the M-profile decoder.
        if self.arch_name == "cortex-m3":
            try:
                self._uc.ctl_set_cpu_model(arm_const.UC_CPU_ARM_CORTEX_M3)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "UnicornBackend: ctl_set_cpu_model(CORTEX_M3) failed (%s)"
                    " — kernel boot may UC_ERR_INSN_INVALID", exc,
                )

        # Plain 32-bit A-profile ARM ("arm"): the generic default core does
        # not implement the full CP15 system-control coprocessor / classic
        # privileged instructions an RTOS reset stub uses (MMU+cache enable,
        # TLB/cache maintenance via mcr p15, banked-mode setup), so deep boot
        # code can hit UC_ERR_INSN_INVALID. Pin a concrete classic core so
        # unicorn uses a decoder that implements them. ARM926EJ-S (ARMv5TEJ)
        # is the typical core in this era of VxWorks PLC/SoC firmware (e.g.
        # the target PLC); override with HAL_ARM_CPU_MODEL=UC_CPU_ARM_<name>.
        if self.arch_name == "arm":
            import os as _os
            model_name = _os.environ.get("HAL_ARM_CPU_MODEL", "UC_CPU_ARM_926")
            self._cpu_model_name = model_name  # recorded in snapshot fingerprint
            model = getattr(arm_const, model_name, None)
            if model is not None:
                try:
                    self._uc.ctl_set_cpu_model(model)
                    log.info("UnicornBackend: ARM CPU model = %s", model_name)
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "UnicornBackend: ctl_set_cpu_model(%s) failed (%s)"
                        " — boot may UC_ERR_INSN_INVALID", model_name, exc,
                    )
            else:
                log.warning("UnicornBackend: unknown HAL_ARM_CPU_MODEL=%r",
                            model_name)

        # PPC64 needs MSR.SF=1 so the CPU decodes 64-bit instructions.
        # Without it, any ld/std fires UC_ERR_EXCEPTION immediately.
        if arch_str == "ppc" and mode_str.startswith("ppc64"):
            msr_reg = self._reg_map.get("msr")
            if msr_reg is not None:
                self._uc.reg_write(msr_reg, 1 << 63)

        for region in self._regions:
            self._map_region(region)

        # ARMv7-M private peripheral bus (PPB) — SCS/NVIC/SCB/SysTick/MPU
        # at 0xE0000000–0xE00FFFFF (1 MB). Cortex-M boot code writes VTOR,
        # AIRCR, SHCSR, NVIC enable bits etc. before our intercepts have
        # any chance to run, so we map the PPB as plain RW memory and let
        # the writes succeed silently. Reads return 0, which is safe for
        # status-poll loops on a stubbed peripheral.
        if self.arch_name == "cortex-m3":
            try:
                self._uc.mem_map(0xE0000000, 0x00100000, _PERM_MAP["rw"])
            except Exception as exc:  # noqa: BLE001
                # Already mapped by an explicit config region — fine.
                log.debug(
                    "UnicornBackend: PPB auto-map skipped (%s)", exc,
                )

        # Global hook to detect breakpoint hits and stop execution
        self._uc.hook_add(
            unicorn.UC_HOOK_CODE,
            self._code_hook,
        )
        # Log unmapped / invalid memory accesses so test firmware crashes
        # produce useful diagnostics instead of opaque UC_ERR_* strings.
        self._uc.hook_add(
            unicorn.UC_HOOK_MEM_READ_UNMAPPED
            | unicorn.UC_HOOK_MEM_WRITE_UNMAPPED
            | unicorn.UC_HOOK_MEM_FETCH_UNMAPPED,
            self._invalid_mem_hook,
        )
        # Log CPU exceptions (unhandled traps, illegal insns, FP faults)
        self._uc.hook_add(unicorn.UC_HOOK_INTR, self._intr_hook)

        # x86 uses *port* I/O (the IN/OUT instructions) for the PC chipset
        # — the 8259 PIC, 16550 UART, 8254 PIT, etc. — in addition to
        # memory-mapped I/O. Unicorn delivers those through dedicated
        # UC_HOOK_INSN hooks rather than the memory hooks. Without them an
        # `out` to an unmodeled port faults the CPU. We absorb them: OUT is
        # a no-op, IN returns 0 (and IN of a UART line-status register is
        # special-cased to report "transmitter empty + data not ready" so
        # the firmware's UART poll loops don't spin forever). This mirrors
        # the AutoPeripheral catch-all policy for MMIO.
        if arch_str == "x86" and x86_const is not None:
            self._port_reads: Dict[int, int] = {}
            self._add_insn_hook(self._x86_in_hook, x86_const.UC_X86_INS_IN)
            self._add_insn_hook(self._x86_out_hook, x86_const.UC_X86_INS_OUT)

        # Diagnostic: HAL_TRACK_READS=1 logs the first read from every SDRAM
        # address that hasn't been written to in this run. Stripped firmware
        # boots that depend on globals initialised by other code paths
        # (e.g. C++ static constructors we can't run) crash when those
        # globals are dereferenced; this log identifies them.
        if _os.environ.get("HAL_TRACK_READS") == "1":
            # Track reads from the .bss region only (above the firmware
            # file's end-of-image). HAL_BSS_START / HAL_BSS_END configure
            # the range; default matches the target PLC layout.
            bss_start = int(_os.environ.get("HAL_BSS_START", "0x20420000"), 16)
            bss_end = int(_os.environ.get("HAL_BSS_END", "0x24000000"), 16)
            self._written: set = set()
            self._read_uninit: dict = {}
            log.error("HAL_TRACK_READS: tracking uninit reads in 0x%x..0x%x",
                      bss_start, bss_end)
            def _track_write(uc, access, addr, size, value, ud):
                if bss_start <= addr < bss_end:
                    for off in range(size):
                        self._written.add(addr + off)
            def _track_read(uc, access, addr, size, value, ud):
                if not (bss_start <= addr < bss_end):
                    return
                if len(self._read_uninit) >= 80:
                    return
                # If any byte in range was previously written, skip.
                if any(addr + off in self._written
                       for off in range(size)):
                    return
                if addr in self._read_uninit:
                    return
                try:
                    pc = uc.reg_read(self._reg_map.get("pc"))
                    lr_reg = self._reg_map.get("lr")
                    lr = uc.reg_read(lr_reg) if lr_reg else 0
                    self._read_uninit[addr] = (pc, lr, size, value)
                    log.error("UNINIT-READ: 0x%08x (size %d, value=0x%x) "
                              "from PC=0x%08x lr=0x%08x",
                              addr, size, value & 0xffffffff, pc, lr)
                except Exception:
                    pass
            self._uc.hook_add(unicorn.UC_HOOK_MEM_WRITE, _track_write)
            self._uc.hook_add(unicorn.UC_HOOK_MEM_READ, _track_read)

        # Diagnostic: HAL_SP_WATCH=1 logs PC whenever SP makes a *large*
        # jump (>= 64 MB), regardless of destination. Catches `mov sp, X` /
        # `ldr sp, [X]` / context-switch ops that warp the stack pointer
        # far away in firmware that runs without full memory-allocator init.
        if _os.environ.get("HAL_SP_WATCH") == "1" and arch_str == "arm":
            sp_state = {"prev": None, "n": 0}
            def _sp_watch(uc, addr, size, ud):
                try:
                    sp = uc.reg_read(self._reg_map.get("sp"))
                except Exception:
                    return
                prev = sp_state["prev"]
                sp_state["prev"] = sp
                if prev is None:
                    return
                # Flag any SP jump of >= 64 MB (large warp, not a normal
                # push/pop). Cap log volume.
                if abs(sp - prev) >= 0x04000000:
                    sp_state["n"] += 1
                    if sp_state["n"] <= 12:
                        log.error("SP-WATCH: PC=0x%08x sp 0x%08x -> 0x%08x "
                                  "(delta %d)", addr, prev, sp, sp - prev)
            self._uc.hook_add(unicorn.UC_HOOK_CODE, _sp_watch)

        # Diagnostic: HAL_PC_SAMPLE=1 records a PC execution histogram so a
        # non-MMIO hang ("stuck where?") can be located. Dumped by
        # dump_pc_sample(). Off by default (no overhead).
        import os as _os
        if _os.environ.get("HAL_PC_SAMPLE"):
            import collections as _c
            self._pc_hist = _c.Counter()
            self._pc_n = 0
            _every = int(_os.environ.get("HAL_PC_SAMPLE_EVERY", "3000000"))

            def _pc_sample(uc, addr, size, ud):
                self._pc_hist[addr & ~1] += 1
                self._pc_n += 1
                if _every and self._pc_n % _every == 0:
                    self.dump_pc_sample()
            self._uc.hook_add(unicorn.UC_HOOK_CODE, _pc_sample)

        # HAL_DET_TICK="<irq>:<chunks>" -- drive the system clock IRQ DETERMINISTICALLY from
        # instruction count (one tick every <chunks> emu_start chunks in cont()) instead of the
        # wall-clock timer thread. Wall-clock ticks fire at nondeterministic instruction
        # boundaries, so run-state/scan scheduling becomes timing-fragile (any bp-handler overhead
        # perturbs it). Tying the tick to instruction count makes scheduling reproducible
        # regardless of handler overhead -> the run-drive stays stable and can be observed.
        self._det_irq = None
        self._det_period = 0
        self._det_chunks = 0
        _det = _os.environ.get("HAL_DET_TICK")
        if _det:
            try:
                _di, _dp = _det.split(":")
                self._det_irq = int(_di, 0)
                self._det_period = max(1, int(_dp, 0))
            except Exception:  # noqa: BLE001
                self._det_irq = None

        # Diagnostic: HAL_LAST_PC=1 keeps a ring of the last basic-block start PCs
        # (low per-block overhead) so that on a UcError the code path leading INTO
        # the fault can be dumped -- essential when the faulting transfer is a
        # `ldr pc,[..]` / stack-return (not caught by the bl/mov-pc call tracer).
        if _os.environ.get("HAL_LAST_PC"):
            import collections as _c2
            _depth = int(_os.environ.get("HAL_LAST_PC_DEPTH", "24"))
            self._last_blocks = _c2.deque(maxlen=_depth)

            def _blk_ring(uc, addr, size, ud):
                self._last_blocks.append(addr & ~1)
            self._uc.hook_add(unicorn.UC_HOOK_BLOCK, _blk_ring)

        # Diagnostic: HAL_WATCH_RANGE="0xLO-0xHI" logs writes into [LO,HI) whose VALUE looks like a
        # bad pointer (into the task-object region 0x2071xxxx-0x2075xxxx, or below 0x20000000 =
        # unmapped) -- i.e. the corruption that overwrites a state-handler/vtable pointer and
        # derails the FSM. Value-filtered to skip the flood of normal object-field writes.
        _wr = _os.environ.get("HAL_WATCH_RANGE")
        if _wr:
            try:
                _rlo, _rhi = (int(x, 0) for x in _wr.split("-"))
                _rpc = self._reg_map.get("pc")

                def _rwatch(uc, access, waddr, wsize, wval, ud):
                    if _rlo <= waddr < _rhi:
                        v = wval & 0xFFFFFFFF
                        if (0x20710000 <= v < 0x20760000) or v < 0x20000000:
                            try:
                                _p = uc.reg_read(_rpc)
                            except Exception:  # noqa: BLE001
                                _p = 0
                            log.error("HAL_WATCH_RANGE: [0x%08x]<-0x%08x (sz%d) PC=0x%08x",
                                      waddr, v, wsize, _p & 0xFFFFFFFF)
                self._uc.hook_add(unicorn.UC_HOOK_MEM_WRITE, _rwatch, begin=_rlo, end=_rhi - 1)
                log.error("HAL_WATCH_RANGE: watching bad-ptr writes into [0x%08x,0x%08x)",
                          _rlo, _rhi)
            except Exception as _e:  # noqa: BLE001
                log.error("HAL_WATCH_RANGE: bad spec %r: %s", _wr, _e)

        # Diagnostic: HAL_CALL_TRACE=<path> logs every bl/blx target seen
        # (call graph). For ARM only -- decodes the instruction at each PC
        # and records (caller_pc, callee_pc, lr_at_call) when a bl fires.
        # Useful for finding cold-init reachability without rescue PC.
        if _os.environ.get("HAL_CALL_TRACE"):
            _trace_path = _os.environ["HAL_CALL_TRACE"]
            self._call_trace_fp = open(_trace_path, "w", buffering=1)
            self._call_trace_seen = set()
            _max_unique = int(_os.environ.get("HAL_CALL_TRACE_MAX", "50000"))

            def _call_trace(uc, addr, size, ud):
                if len(self._call_trace_seen) >= _max_unique:
                    return
                try:
                    insn_bytes = uc.mem_read(addr, 4)
                except Exception:
                    return
                w = int.from_bytes(insn_bytes, "little")
                # ARM bl: cond=any, opcode=0xb (bl), 24-bit signed offset
                cond = (w >> 28) & 0xf
                opc = (w >> 24) & 0xf
                if cond == 0xf or opc != 0xb:
                    return
                off = w & 0xffffff
                if off & 0x800000: off -= 0x1000000
                tgt = addr + 8 + (off << 2)
                key = (addr & ~1, tgt)
                if key in self._call_trace_seen:
                    return
                self._call_trace_seen.add(key)
                self._call_trace_fp.write(
                    "bl 0x%08x -> 0x%08x\n" % (addr & ~1, tgt))
            self._uc.hook_add(unicorn.UC_HOOK_CODE, _call_trace)

            # ALSO trace indirect calls: pattern is `mov lr, pc; mov pc, Rn`
            # (ARMv4-era vfunc dispatch, used by the firmware's C++ thunks).
            # We capture this by hooking AFTER `mov pc, ip` executes -- when
            # PC differs from expected fall-through, it was an indirect call.
            self._last_pc_was_movpc = False
            self._last_movpc_pc = 0

            def _indirect_trace(uc, addr, size, ud):
                if len(self._call_trace_seen) >= _max_unique:
                    return
                # Was the previous instruction `mov pc, ip` (or similar)?
                if self._last_pc_was_movpc:
                    self._last_pc_was_movpc = False
                    src = self._last_movpc_pc
                    key = (src | 1, addr & ~1)    # mark indirect with low bit on src
                    if key not in self._call_trace_seen:
                        self._call_trace_seen.add(key)
                        self._call_trace_fp.write(
                            "indirect 0x%08x -> 0x%08x\n" % (src, addr & ~1))
                try:
                    insn_bytes = uc.mem_read(addr, 4)
                except Exception:
                    return
                w = int.from_bytes(insn_bytes, "little")
                # mov pc, Rn: 0xe1a0f00n (n = 0..14, cond=e)
                # bx Rn:      0xe12fff1n
                if (w & 0xffffff00) == 0xe1a0f000 or (w & 0xfffffff0) == 0xe12fff10:
                    self._last_pc_was_movpc = True
                    self._last_movpc_pc = addr & ~1
            self._uc.hook_add(unicorn.UC_HOOK_CODE, _indirect_trace)

        # HAL_PIN_REGS="0xADDR=0xVALUE[,0xADDR=0xVALUE...]": model read-only
        # hardware/boot-ROM latch registers that live in RAM space but the
        # firmware never writes (e.g. an RTOS kernel "system-ready" flag
        # in RAM, many readers, no writer). A boot seed gets clobbered by
        # the firmware's .bss-clearing memset; this hook re-pins the value on
        # every read so the load always returns it, exactly like a HW control
        # register. (Unicorn's read hook fires before the load samples memory,
        # so writing here makes the subsequent load return the pinned value.)
        _pin = _os.environ.get("HAL_PIN_REGS")
        if _pin and arch_str == "arm":
            pins = {}
            for tok in _pin.split(","):
                tok = tok.strip()
                if not tok or "=" not in tok:
                    continue
                a_s, v_s = tok.split("=", 1)
                try:
                    pins[int(a_s, 0)] = int(v_s, 0)
                except ValueError:
                    log.warning("HAL_PIN_REGS: bad token %r", tok)
            if pins:
                lo = min(pins); hi = max(pins) + 4
                log.info("HAL_PIN_REGS: pinning %d register(s): %s",
                         len(pins), ", ".join("0x%08x=0x%08x" % (a, v)
                                              for a, v in pins.items()))
                # Optional PC gate: HAL_PIN_PC_LO/HI restrict pinning to reads
                # whose PC is in [LO,HI). Needed for phase-dependent flags that
                # must be FALSE during init and TRUE only at a specific point
                # (e.g. the multitasking-start dispatch) -- pinning globally
                # corrupts the init logic that expects the flag clear.
                _pc_lo = _os.environ.get("HAL_PIN_PC_LO")
                _pc_hi = _os.environ.get("HAL_PIN_PC_HI")
                pc_lo = int(_pc_lo, 0) if _pc_lo else None
                pc_hi = int(_pc_hi, 0) if _pc_hi else None
                pc_reg = self._reg_map.get("pc")
                # HAL_PIN_ARM_PC=0xADDR: latch the pins ON the first time this PC
                # executes, and keep them on thereafter. Models a boot-ROM/HW
                # flag that transitions to "ready" at a single point (the
                # scheduler-start entry) and stays set -- cleaner than a PC
                # range for a flag that must be false through ALL of init and
                # true through ALL of multitasking.
                _arm = _os.environ.get("HAL_PIN_ARM_PC")
                arm_pc = int(_arm, 0) if _arm else None
                pin_state = {"armed": arm_pc is None}
                self._pin_arm_pc = arm_pc
                self._pin_state = pin_state
                if arm_pc is not None:
                    # Armed from _code_hook (fires per-instruction) -- a tight
                    # begin/end UC_HOOK_CODE doesn't reliably fire.
                    def _arm_hook(uc, addr, size, ud):
                        if (addr & ~1) == arm_pc and not pin_state["armed"]:
                            pin_state["armed"] = True
                            log.info("HAL_PIN_REGS: armed at 0x%08x", addr)
                    self._uc.hook_add(unicorn.UC_HOOK_CODE, _arm_hook)
                def _pin_read(uc, access, addr, size, value, ud):
                    if not pin_state["armed"]:
                        return
                    if pc_lo is not None:
                        try:
                            pc = uc.reg_read(pc_reg)
                        except Exception:
                            return
                        if not (pc_lo <= pc < pc_hi):
                            return
                    for pa, pv in pins.items():
                        if addr <= pa < addr + size or pa <= addr < pa + 4:
                            try:
                                uc.mem_write(pa, pv.to_bytes(4, "little"))
                            except Exception:
                                pass
                self._uc.hook_add(unicorn.UC_HOOK_MEM_READ, _pin_read,
                                  begin=lo, end=hi - 1)

        # Diagnostic: HAL_WATCH_WRITE="0xADDR[,0xADDR...]" logs PC + value on every
        # write to those addresses ("who writes this field?"). For finding skipped
        # object-init writes (e.g. a TCB OBJ_CORE self-ptr never set).
        _ww = _os.environ.get("HAL_WATCH_WRITE")
        if _ww and arch_str == "arm":
            _waddrs = set(int(x, 0) for x in _ww.split(",") if x.strip())
            _wlo = min(_waddrs); _whi = max(_waddrs) + 4
            _wpc = self._reg_map.get("pc")
            def _watch_write(uc, access, addr, size, value, ud):
                if any(a <= addr < a + size or addr <= a < addr + 4 for a in _waddrs):
                    try:
                        pc = uc.reg_read(_wpc)
                    except Exception:
                        pc = 0
                    log.error("WATCH-WRITE: [0x%08x]<=0x%08x (size %d) PC=0x%08x",
                              addr, value, size, pc)
            self._uc.hook_add(unicorn.UC_HOOK_MEM_WRITE, _watch_write,
                              begin=_wlo, end=_whi - 1)

        # Diagnostic: HAL_STEP_TRACE="0xLO-0xHI[:path]" single-step-logs every
        # instruction whose PC is in [LO,HI): PC | sp | r0-r3 | sl | ip | lr.
        # For pinning frame/register state to the exact instruction (e.g. a
        # context-switch TCB load or a trap-style ldm epilogue).
        _st = _os.environ.get("HAL_STEP_TRACE")
        if _st and arch_str == "arm":
            _spec = _st.split(":", 1)
            _lo, _hi = (int(x, 0) for x in _spec[0].split("-"))
            _stf = open(_spec[1] if len(_spec) > 1 else "/tmp/hal_step_trace.txt", "w")
            _st_n = {"n": 0}
            _rmap = self._reg_map
            def _step_trace(uc, addr, size, ud):
                if not (_lo <= addr < _hi) or _st_n["n"] >= 200000:
                    return
                _st_n["n"] += 1
                try:
                    vals = tuple(uc.reg_read(_rmap.get(r)) for r in
                                 ("sp", "r0", "r1", "r2", "r3", "r10", "r12", "lr"))
                    _stf.write("0x%08x sp=0x%08x r0=0x%08x r1=0x%08x r2=0x%08x r3=0x%08x "
                               "sl=0x%08x ip=0x%08x lr=0x%08x\n" % ((addr,) + vals))
                    _stf.flush()
                except Exception:
                    pass
            self._uc.hook_add(unicorn.UC_HOOK_CODE, _step_trace)

    def dump_pc_sample(self, top: int = 10) -> None:
        hist = getattr(self, "_pc_hist", None)
        if not hist:
            return
        log.info("PC sample (top %d most-executed):", top)
        for pc, n in hist.most_common(top):
            log.info("  0x%08x  x%d", pc, n)

    def _intr_hook(self, uc, intno, user_data):
        try:
            pc = self.read_register("pc")
        except Exception:
            pc = -1
        # x86: VxWorks (and most x86 kernels) reload segment selectors from
        # a GDT they build at boot — typically via a far `iretd`/`retf`/
        # `ljmp` to a flat code selector. Unicorn's x86 model has no GDT
        # loaded at reset, so the selector reference raises a #GP (vector
        # 13) before the control transfer completes. We emulate a flat
        # segmentation model: decode the segment-changing instruction's
        # frame off the stack and resume at the target EIP, ignoring the
        # (flat) selector. This is what lets the RTU image cross from
        # _start into usrInit. See _x86_handle_seg_fault.
        if (self.arch_name == "x86" and pc != -1
                and self._x86_handle_seg_fault(uc, pc)):
            return
        # On cortex-m3, an ISR returning via `bx lr` jumps to an
        # EXC_RETURN magic value (top nibble 0xF). Unicorn raises an
        # exception here rather than firing the fetch-unmapped hook,
        # so handle it the same way and unwind the synthetic frame.
        if (self.arch_name == "cortex-m3"
                and pc != -1
                and self._maybe_handle_exc_return(pc)):
            return  # _maybe_handle_exc_return already called emu_stop
        # Opt-in recovery: a Thumb SVC (high byte 0xDF) from instrumented
        # firmware (e.g. P2IM aflCall). When the SVC traps, unicorn reports
        # pc at the *next* instruction, so the SVC opcode is at pc or pc-2.
        # We zero the return register and continue without stopping (pc has
        # already advanced past the SVC), rather than aborting the run.
        if self.skip_svc and pc != -1:
            try:
                for probe in (pc - 2, pc):
                    op = bytes(uc.mem_read(probe, 2))
                    if len(op) == 2 and op[1] == 0xDF:  # Thumb SVC
                        # ensure pc is past the SVC, then resume
                        if probe == pc:
                            self.write_register("pc", pc + 2)
                        self.write_register("r0", 0)
                        return
            except Exception:  # noqa: BLE001
                pass
        # Opt-in PC-write emulation (HAL_EMULATE_PC_WRITE=1): the faulting
        # instruction is `mov{cond} pc, Rm` (LSL #0) — a native-ARM register
        # return/indirect-branch unicorn raised here instead of executing. Read
        # Rm (still the program's banked-out value at intr time) and branch there.
        if (self._emulate_pc_write and self.arch_name == "arm"
                and pc not in (-1, 0)):
            try:
                instr = int.from_bytes(bytes(uc.mem_read(pc, 4)), "little")
            except Exception:  # noqa: BLE001
                instr = 0
            if (instr & 0x0FFFFFF0) == 0x01A0F000:   # mov{cond} pc, Rm
                rm = instr & 0xF
                name = ("r%d" % rm if rm <= 12
                        else {13: "sp", 14: "lr", 15: "pc"}[rm])
                try:
                    target = self.read_register(name) & 0xFFFFFFFE
                    self.write_register("pc", target)
                    self._pc_write_emulated += 1
                    if self._pc_write_emulated <= 8 or self._pc_write_emulated % 1000 == 0:
                        log.info("UnicornBackend: emulated PC-write `mov pc,%s` at "
                                 "0x%08x -> 0x%08x (#%d)", name, pc, target,
                                 self._pc_write_emulated)
                    return
                except Exception as e:  # noqa: BLE001
                    log.info("UnicornBackend: PC-write emulate failed at 0x%08x: %s", pc, e)
        # Opt-in bad-call recovery (HAL_RECOVER_BAD_CALLS=1): on A-profile
        # ARM, an indirect call through a `_func_` hook bound to a garbage
        # (data) address faults on the first fetch — delivered here as an
        # exception, with lr still valid (the wrapper set it via `mov lr,pc`
        # and the bad target ran nothing). Return to lr instead of aborting,
        # capped per fault PC so a genuinely wedged address doesn't loop.
        if (self._recover_bad_calls and self.arch_name == "arm"
                and pc not in (-1, 0)):
            lr_reg = self._reg_map.get("lr")
            lr = (uc.reg_read(lr_reg) & ~1) if lr_reg else 0
            self._bad_call_recover[pc] = self._bad_call_recover.get(pc, 0) + 1
            if lr and lr != pc and self._bad_call_recover[pc] <= 4:
                log.info("UnicornBackend: bad call (intr %d) at 0x%08x -> "
                         "return lr=0x%08x", intno, pc, lr)
                self.write_register("pc", lr)
                return
        # Opt-in MMU flat-fallback (HAL_MMU_FLAT_FALLBACK=1): on the first ARM
        # data/prefetch abort, DISABLE the MMU (clear SCTLR.M) so the rest of
        # the run is flat (VA==PA). unicorn's CP15/TTBR handling is unreliable
        # for this firmware (TTBR0 reads 0), translation-faulting on pages that
        # are physically present; since the firmware's mappings are identity,
        # running flat is equivalent and avoids both data- and prefetch-abort
        # loops. We do this LATE (on the fault, after usrMmuInit/vmLib is up),
        # not by skipping usrMmuInit, so vmLib stays initialised. Falls back to
        # per-instruction flat-completion of the load/store if SCTLR can't be
        # cleared on this unicorn build.
        if (self._mmu_flat_fallback and self.arch_name == "arm"
                and intno in (3, 4) and pc not in (-1, 0)):
            if self._mmu_disable(uc):
                return  # MMU now off -> faulting instr re-executes flat
            if intno == 4 and self._mmu_flat_complete(uc, pc):
                return
        log.error("UnicornBackend: CPU exception/interrupt %d at pc=0x%x",
                  intno, pc)
        uc.emu_stop()

    def _mmu_disable(self, uc) -> bool:
        """Clear SCTLR.M (and TLB-relevant bits) to turn off ARM MMU
        translation so execution proceeds flat (VA==PA). Idempotent: once
        done, returns True on subsequent calls without re-touching CP15.
        Returns False if CP15 SCTLR isn't accessible on this unicorn build."""
        if getattr(self, "_mmu_off", False):
            return True
        from unicorn import arm_const
        # CP15 SCTLR = coproc 15, crn=c1, crm=c0, opc1=0, opc2=0.
        spec = (15, 0, 0, 1, 0, 0, 0)
        try:
            sctlr = uc.reg_read(arm_const.UC_ARM_REG_CP_REG, spec)
        except Exception:  # noqa: BLE001
            return False
        if not (sctlr & 0x1):
            # MMU already off — nothing to do, but a fault still happened, so
            # this isn't our case; let the caller try other handlers.
            return False
        try:
            uc.reg_write(arm_const.UC_ARM_REG_CP_REG, spec + (sctlr & ~0x1,))
        except Exception:  # noqa: BLE001
            return False
        # Verify it took.
        try:
            if uc.reg_read(arm_const.UC_ARM_REG_CP_REG, spec) & 0x1:
                return False
        except Exception:  # noqa: BLE001
            return False
        self._mmu_off = True
        log.info("UnicornBackend: MMU flat-fallback -> disabled MMU "
                 "(SCTLR.M cleared 0x%x -> 0x%x); running flat from here",
                 sctlr, sctlr & ~0x1)
        return True

    # capstone ARM register name -> unicorn UC_ARM_REG_* id
    @staticmethod
    def _arm_reg_id(name: str):
        from unicorn import arm_const
        alias = {"sb": "r9", "sl": "r10", "fp": "r11", "ip": "r12",
                 "r13": "sp", "r14": "lr", "r15": "pc"}
        n = alias.get(name.lower(), name.lower())
        return getattr(arm_const, "UC_ARM_REG_" + n.upper(), None)

    def _mmu_flat_complete(self, uc, pc: int) -> bool:
        """Emulate a single faulting ARM load/store flat (VA==PA) and advance
        PC by 4. Returns True if it handled the instruction. Only acts when the
        faulting address is backed in physical memory (uc.mem_read works), which
        is the 'MMU translation missing but page present' case. Unhandled forms
        (LDM/STM, etc.) return False so the caller aborts as before."""
        try:
            import capstone
            import capstone.arm as cs_arm
        except ImportError:
            return False
        cs = getattr(self, "_cs_arm", None)
        if cs is None:
            cs = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_ARM)
            cs.detail = True
            self._cs_arm = cs
        try:
            ins = next(cs.disasm(bytes(uc.mem_read(pc, 4)), pc), None)
        except Exception:  # noqa: BLE001
            return False
        if ins is None:
            return False
        # Map the instruction to (access size, is_load, signed).
        I = cs_arm
        sizes = {
            I.ARM_INS_LDR: (4, True, False), I.ARM_INS_STR: (4, False, False),
            I.ARM_INS_LDRB: (1, True, False), I.ARM_INS_STRB: (1, False, False),
            I.ARM_INS_LDRH: (2, True, False), I.ARM_INS_STRH: (2, False, False),
            I.ARM_INS_LDRSB: (1, True, True), I.ARM_INS_LDRSH: (2, True, True),
        }
        if ins.id not in sizes:
            return False
        size, is_load, signed = sizes[ins.id]
        # Operands: a register operand (dest for load / src for store) and a
        # memory operand {base, index, lshift, disp}.
        reg_op = None
        mem_op = None
        for o in ins.operands:
            if o.type == I.ARM_OP_REG and reg_op is None:
                reg_op = o
            elif o.type == I.ARM_OP_MEM:
                mem_op = o
        if reg_op is None or mem_op is None:
            return False
        m = mem_op.mem
        base_id = self._arm_reg_id(cs.reg_name(m.base)) if m.base else None
        if base_id is None:
            return False
        addr = uc.reg_read(base_id) & 0xFFFFFFFF
        if m.index:
            idx_id = self._arm_reg_id(cs.reg_name(m.index))
            if idx_id is None:
                return False
            idxval = uc.reg_read(idx_id) & 0xFFFFFFFF
            addr += (idxval << m.lshift) if m.lshift else idxval
        addr = (addr + m.disp) & 0xFFFFFFFF
        # Only act if the physical page is present (translation-missing case).
        try:
            data = bytes(uc.mem_read(addr, size))
        except Exception:  # noqa: BLE001
            return False  # genuinely unmapped -> real fault
        rid = self._arm_reg_id(cs.reg_name(reg_op.reg))
        if rid is None:
            return False
        if is_load:
            val = int.from_bytes(data, "little")
            if signed and (val & (1 << (size * 8 - 1))):
                val -= 1 << (size * 8)
            uc.reg_write(rid, val & 0xFFFFFFFF)
        else:
            val = uc.reg_read(rid) & ((1 << (size * 8)) - 1)
            uc.mem_write(addr, val.to_bytes(size, "little"))
        # Pre/post-indexed writeback updates the base register with `addr`
        # (pre) — capstone exposes writeback via ins.writeback.
        if getattr(ins, "writeback", False):
            uc.reg_write(base_id, addr)
        uc.reg_write(self._reg_map["pc"], pc + 4)
        self._mmu_flat_count[pc] = self._mmu_flat_count.get(pc, 0) + 1
        if self._mmu_flat_count[pc] <= 3:
            log.info("UnicornBackend: MMU flat-fallback %s @0x%08x addr=0x%08x "
                     "(x%d)", ins.mnemonic, pc, addr, self._mmu_flat_count[pc])
        return True

    # ------------------------------------------------------------------
    # x86 port I/O (IN / OUT) — catch-all so the PC chipset doesn't fault
    # ------------------------------------------------------------------

    # Standard PC-AT 16550 UART line-status register offsets (COM1 0x3F8,
    # COM2 0x2F8, COM3 0x3E8, COM4 0x2E8). LSR is base+5; bit5 (THRE) and
    # bit6 (TEMT) report the transmitter is ready. We return those set so
    # firmware that polls "is the UART ready to send?" makes progress, and
    # leave bit0 (data-ready) clear so receive polls fall through.
    _X86_UART_BASES = (0x3F8, 0x2F8, 0x3E8, 0x2E8)
    _X86_UART_LSR_THRE_TEMT = 0x60  # bits 5+6

    def _add_insn_hook(self, callback: Callable, insn_id: int) -> None:
        """Register a UC_HOOK_INSN hook for a specific instruction id.
        The instruction id is passed via the aux1 parameter on this
        unicorn build; older bindings used a positional `arg1`. Try the
        keyword forms in turn and warn (but don't abort) if none work."""
        for kw in ("aux1", "arg1"):
            try:
                self._uc.hook_add(unicorn.UC_HOOK_INSN, callback,
                                  **{kw: insn_id})
                return
            except TypeError:
                continue
            except Exception as exc:  # noqa: BLE001
                log.warning("UnicornBackend: x86 INSN hook (id=%d) failed: %s",
                            insn_id, exc)
                return
        log.warning("UnicornBackend: could not register x86 INSN hook id=%d "
                    "(unicorn binding lacks aux1/arg1); IN/OUT may fault",
                    insn_id)

    def _x86_in_hook(self, uc, port, size, user_data):
        """Handle an `in` from an I/O port. Return value is written back
        into the destination register by unicorn (return it from here)."""
        val = 0
        for base in self._X86_UART_BASES:
            if port == base + 5:  # Line Status Register
                val = self._X86_UART_LSR_THRE_TEMT
                break
        log.debug("x86 IN  port=0x%x size=%d -> 0x%x", port, size, val)
        return val

    def _x86_out_hook(self, uc, port, size, value, user_data):
        """Handle an `out` to an I/O port. Capture printable bytes written
        to a UART transmit-holding register (base+0) as console output;
        otherwise drop the write (no-op, like the MMIO catch-all)."""
        for base in self._X86_UART_BASES:
            if port == base:  # Transmit Holding Register
                low = value & 0xFF
                if low == 0x0A or low == 0x0D or 0x20 <= low < 0x7F:
                    buf = getattr(self, "_x86_uart_buf", None)
                    if buf is None:
                        buf = self._x86_uart_buf = bytearray()
                    if low == 0x0A:
                        line = buf.decode("latin-1").rstrip("\r")
                        log.info("x86 UART(port 0x%x): %s", base, line)
                        buf.clear()
                    elif low != 0x0D:
                        buf.append(low)
                break
        log.debug("x86 OUT port=0x%x size=%d value=0x%x", port, size, value)

    def _x86_handle_seg_fault(self, uc, pc: int) -> bool:
        """Flat-segmentation recovery for an x86 #GP at a segment-changing
        instruction. Decodes the on-stack frame and resumes at the target
        EIP, treating all selectors as a flat 0-based segment (which is how
        the firmware's own GDT is configured once it's loaded).

        Handled forms:
          iretd  (0xCF)          frame: [esp]=EIP [esp+4]=CS [esp+8]=EFLAGS
          retf   (0xCB)          frame: [esp]=EIP [esp+4]=CS
          retf N (0xCA imm16)    as retf, then esp += N
          ljmp m16:32 (0xEA …)   far direct jump: operand carries EIP+CS

        Returns True when it recognised and resolved the fault."""
        try:
            opc = bytes(uc.mem_read(pc, 1))
        except Exception:  # noqa: BLE001
            return False
        if not opc:
            return False
        op = opc[0]
        try:
            esp = self.read_register("esp")
            if op == 0xCF:  # iretd
                eip, _cs, eflags = struct.unpack(
                    "<III", bytes(uc.mem_read(esp, 12)))
                self.write_register("esp", esp + 12)
                self.write_register("eflags", eflags | 0x2)
                self.write_register("eip", eip)
            elif op in (0xCB, 0xCA):  # retf / retf imm16
                eip, _cs = struct.unpack("<II", bytes(uc.mem_read(esp, 8)))
                pop = 8
                if op == 0xCA:
                    pop += struct.unpack("<H", bytes(uc.mem_read(pc + 1, 2)))[0]
                self.write_register("esp", esp + pop)
                self.write_register("eip", eip)
            elif op == 0xEA:  # ljmp ptr16:32
                eip = struct.unpack("<I", bytes(uc.mem_read(pc + 1, 4)))[0]
                self.write_register("eip", eip)
            else:
                return False
        except Exception as exc:  # noqa: BLE001
            log.warning("x86 seg-fault recovery at pc=0x%x op=0x%02x "
                        "failed: %s", pc, op, exc)
            return False
        new_eip = self.read_register("eip")
        # An `iretd` (0xCF) here unwinds an interrupt frame — either back
        # to the code the tick interrupted, or (via the VxWorks intExit
        # reschedule) into a freshly-dispatched task. Either way the
        # clock ISR has finished, so clear the X86PicController's
        # re-entrancy guard to let the next tick in.
        if op == 0xCF:
            ctrl = getattr(self, "_irq_controller", None)
            if ctrl is not None and hasattr(ctrl, "on_isr_return"):
                ctrl.on_isr_return()
        log.info("x86: flat-segment recovery for op=0x%02x at pc=0x%08x "
                 "-> resume eip=0x%08x", op, pc, new_eip)
        # Restart emu_start at the recovered EIP (cont() re-enters).
        uc.emu_stop()
        self._x86_resume_eip = new_eip
        return True

    def _invalid_mem_hook(self, uc, access, addr, size, value, user_data):
        """Intercept invalid memory accesses. On cortex-m, a fetch from
        an EXC_RETURN magic address is the ISR returning — we unwind
        the pushed exception frame and resume at the saved PC. Other
        invalid accesses are logged and the emulator aborts.

        With HAL_MAP_UNMAPPED=1, read/write to a gap is treated as zero-
        memory: we map a 4 KB page on-demand and return True so unicorn
        re-runs the load/store against it. This is the same catch-all
        policy used for stubbed MMIO regions, extended to
        arbitrary gaps so a stray ld/st through a garbage pointer doesn't
        crash boot. Fetch_unmapped is still fatal — executing zero pages
        would walk forever; the recover_bad_calls path handles those."""
        if (access == unicorn.UC_MEM_FETCH_UNMAPPED
                and self._maybe_handle_exc_return(addr)):
            return True  # resolved — unicorn will not abort
        try:
            pc = self.read_register("pc")
        except Exception:
            pc = -1
        kind = {
            unicorn.UC_MEM_READ_UNMAPPED: "read",
            unicorn.UC_MEM_WRITE_UNMAPPED: "write",
            unicorn.UC_MEM_FETCH_UNMAPPED: "fetch",
        }.get(access, f"access({access})")
        # On-demand zero-page mapping for read/write to gaps (opt-in).
        # When HAL_MAP_UNMAPPED is set and the lazy map succeeds, this is
        # a designed recovery -- log at WARNING, not ERROR. Genuine
        # unrecoverable cases (write to read-only, fetch from unmapped,
        # HAL_MAP_UNMAPPED unset) still log at ERROR before aborting.
        import os as _os
        if (_os.environ.get("HAL_MAP_UNMAPPED") == "1"
                and access in (unicorn.UC_MEM_READ_UNMAPPED,
                               unicorn.UC_MEM_WRITE_UNMAPPED)):
            try:
                page = 0x1000
                base = addr & ~(page - 1)
                self._uc.mem_map(base, page, 7)  # rwx
                log.warning("UnicornBackend: lazily mapped zero page at 0x%x "
                            "(rwx) -- unmapped %s at 0x%x size %d from "
                            "pc=0x%x", base, kind, addr, size, pc)
                return True
            except Exception as _e:
                log.error("UnicornBackend: unmapped %s at 0x%x (size %d, "
                          "value 0x%x) from pc=0x%x; lazy map failed: %s",
                          kind, addr, size, value, pc, _e)
                return False
        log.error("UnicornBackend: unmapped %s at 0x%x (size %d, value 0x%x) "
                  "from pc=0x%x", kind, addr, size, value, pc)
        if access == unicorn.UC_MEM_FETCH_UNMAPPED:
            # Derail diagnosis: dump lr + the stack top so we can see where the bad PC came from
            # (a corrupted return address / setjmp jmp_buf pops the garbage PC).
            try:
                lr_reg = self._reg_map.get("lr")
                sp = self.read_register("sp") & 0xFFFFFFFF
                lr = (self._uc.reg_read(lr_reg) & 0xFFFFFFFF) if lr_reg else 0
                stk = []
                for _o in range(0, 32, 4):
                    try:
                        stk.append(int.from_bytes(self._uc.mem_read(sp + _o, 4), "little"))
                    except Exception:  # noqa: BLE001
                        stk.append(0xFFFFFFFF)
                lb = getattr(self, "_last_blocks", None)
                log.error("UnicornBackend: FETCH-DERAIL lr=0x%08x sp=0x%08x stack=[%s]%s",
                          lr, sp, " ".join("0x%08x" % w for w in stk),
                          ("" if not lb else " lastblocks=" + " -> ".join("0x%08x" % p for p in lb)))
            except Exception:  # noqa: BLE001
                pass
        return False  # abort

    def _map_region(self, region: MemoryRegion) -> None:
        perm = _PERM_MAP.get(region.permissions.lower(), 0x7)
        # Unicorn requires page-aligned base + size, and refuses any
        # overlap with an existing mapping. Halucinator configs (and
        # QEMU's configurable machine) freely overlap regions because
        # later mappings override earlier ones in QEMU. To bridge the
        # gap, we map this region only over pages that aren't already
        # mapped by an earlier region in self._regions.
        page = 0x1000
        base = region.base_addr & ~(page - 1)
        end = (region.base_addr + region.size + page - 1) & ~(page - 1)
        # Collect already-mapped page ranges.
        mapped = [
            ((r.base_addr & ~(page - 1)),
             ((r.base_addr + r.size + page - 1) & ~(page - 1)))
            for r in self._regions if r is not region
        ]
        cursor = base
        for lo, hi in sorted(mapped):
            if hi <= cursor or lo >= end:
                continue
            if lo > cursor:
                self._safe_map(cursor, min(lo, end) - cursor, perm, region)
            cursor = max(cursor, hi)
            if cursor >= end:
                break
        if cursor < end:
            self._safe_map(cursor, end - cursor, perm, region)

        if region.file:
            try:
                with open(region.file, "rb") as fh:
                    data = fh.read(region.size)
                self._uc.mem_write(region.base_addr, data)
                log.debug("Loaded %s → 0x%x", region.file, region.base_addr)
            except OSError as exc:
                log.warning("Could not load file %s: %s", region.file, exc)

        # Wire MMIO hooks if provided
        if region.read_hook or region.write_hook:
            hook_type = 0
            if region.read_hook:
                hook_type |= unicorn.UC_HOOK_MEM_READ
            if region.write_hook:
                hook_type |= unicorn.UC_HOOK_MEM_WRITE
            h = self._uc.hook_add(
                hook_type,
                self._make_mmio_hook(region),
                begin=region.base_addr,
                end=region.base_addr + region.size - 1,
            )
            self._mmio_hooks[region.name] = h

    def _safe_map(self, base: int, size: int, perm: int,
                  region: MemoryRegion) -> None:
        """mem_map(base, size, perm) with friendly diagnostics on failure."""
        if size <= 0:
            return
        try:
            self._uc.mem_map(base, size, perm)
        except Exception as exc:  # noqa: BLE001
            log.warning("mem_map 0x%x size 0x%x (for region %s): %s",
                        base, size, region.name, exc)

    def _make_mmio_hook(self, region: MemoryRegion) -> Callable:
        def _hook(uc, access, addr, size, value, user_data):
            offset = addr - region.base_addr
            if access == unicorn.UC_MEM_READ and region.read_hook:
                result = region.read_hook(offset, size)
                if result is not None:
                    data = result.to_bytes(size, "little")
                    uc.mem_write(addr, data)
            elif access == unicorn.UC_MEM_WRITE and region.write_hook:
                region.write_hook(offset, size, value)
        return _hook

    def _code_hook(self, uc, addr: int, size: int, user_data: Any) -> None:
        """Called for every instruction; checks if addr is a breakpoint."""
        # Thumb bit lives in the low bit of PC on 32-bit ARM; for other archs
        # instructions are at least 2-byte aligned so masking bit 0 is a no-op.
        pc = addr & ~1
        if pc in self._breakpoints:
            # One-shot skip: an observe-only handler just ran at this bp and
            # asked to resume the real function. Let this single instruction
            # execute without stopping; the bp re-arms for the next hit.
            if pc == self._skip_bp_once:
                self._skip_bp_once = None
                return
            self._stopped = True
            self._bp_hit_addr = pc
            uc.emu_stop()
            return

        # RAM-flag spin breaker (opt-in: HAL_BREAK_RAM_SPINS=1). A tight loop
        # confined to a small PC window for many iterations that polls a RAM
        # location (a flag set by an ISR/task/another SMP core we don't run).
        # Poke the loaded memory non-zero so the firmware's own compare exits.
        if self._break_ram_spins:
            self._spin_pcs.add(pc)
            self._spin_total += 1
            if self._spin_total >= self._spin_limit:
                if len(self._spin_pcs) <= self._spin_distinct_max:
                    # Few distinct PCs over a long window => a spin (possibly
                    # across a call). Poke the RAM the loop's loads read.
                    self._break_ram_spin(uc, set(self._spin_pcs))
                self._spin_pcs.clear()
                self._spin_total = 0

        # Generic non-MMIO loop breaker (opt-in via auto_recover_loops).
        # The MMIO breaker handles status-poll
        # spins; this handles the *non*-MMIO ones — `while(uwTick<t)`,
        # `while(millis()<t)`, HAL_GetTick timeouts — that confine the PC to
        # a tiny window. After `_loop_limit` instructions stuck in a <=64-byte
        # window we force a return (pc <- lr) to escape the wait, capped by
        # `_loop_recover_budget` so a genuinely long computation isn't
        # repeatedly hijacked.
        if not getattr(self, "auto_recover_loops", False):
            return
        lo = self._loop_lo
        if lo <= pc <= lo + 64:
            self._loop_count += 1
            if self._loop_count > self._loop_limit and self._loop_recover_budget > 0:
                lr_reg = self._reg_map.get("lr")
                if lr_reg is not None:
                    lr = uc.reg_read(lr_reg)
                    self._loop_recover_budget -= 1
                    log.info("UnicornBackend: non-MMIO loop at 0x%08x stuck "
                             "%d insns -> return to lr=0x%08x",
                             pc, self._loop_count, lr & ~1)
                    self._loop_lo = -1
                    self._loop_count = 0
                    uc.reg_write(self._reg_map["pc"], lr & ~1 | (lr & 1))
        else:
            self._loop_lo = pc
            self._loop_count = 1

    def _break_ram_spin(self, uc: Any, pcs: set) -> None:
        """A tight loop has spun far too long — a software delay
        (`while(i<N){i++}`) or a wait on a flag set by a context we don't run
        (ISR / task / another SMP core). Escape it by jumping to the loop's
        natural exit: find the loop-back branch (the highest-address branch
        whose target lies back inside the loop) and set PC to its fall-through
        (branch+4). That's a static, valid continuation — exactly where the
        loop goes when its condition finally fails — so unlike forcing pc<-lr
        it can't land on a stale/garbage address. Poking the loaded memory was
        unreliable: a delay loop's own `add;str` immediately overwrites it.

        Cache the exit so a re-entered same loop escalates rather than looping
        the breaker forever."""
        try:
            import capstone
            import capstone.arm as cs_arm
        except ImportError:
            return
        cs = getattr(self, "_cs_arm", None)
        if cs is None:
            cs = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_ARM)
            cs.detail = True
            self._cs_arm = cs
        lo, hi = min(pcs), max(pcs)
        if hi - lo > 0x400:        # not a tight loop; don't guess
            return
        tail_branch = None
        for pc in sorted(pcs, reverse=True):
            try:
                ins = next(cs.disasm(bytes(uc.mem_read(pc, 4)), pc), None)
            except Exception:  # noqa: BLE001
                continue
            if ins is None or ins.id not in (cs_arm.ARM_INS_B,):
                continue
            tgt = next((o.imm for o in ins.operands
                        if o.type == cs_arm.ARM_OP_IMM), None)
            if tgt is not None and lo <= tgt <= pc:   # backward branch = loopback
                tail_branch = pc
                break
        if tail_branch is None:
            return
        exit_pc = tail_branch + 4
        skipped = getattr(self, "_skipped_spins", None)
        if skipped is None:
            skipped = self._skipped_spins = {}
        skipped[tail_branch] = skipped.get(tail_branch, 0) + 1
        try:
            uc.reg_write(self._reg_map["pc"], exit_pc)
            log.info("UnicornBackend: spin loop [0x%08x..0x%08x] -> skip to "
                     "exit 0x%08x (x%d)", lo, hi, exit_pc, skipped[tail_branch])
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # Memory
    # ------------------------------------------------------------------

    def add_memory_region(self, region: MemoryRegion) -> None:
        self._regions.append(region)
        if self._uc is not None:
            self._map_region(region)

    # ------------------------------------------------------------------
    # Snapshot / restore  (Layer 1, native fast path)
    #
    # unicorn's context_save() captures the full CPU context and mem_regions()
    # + mem_read give a bulk copy of every mapped page, so save/restore is a
    # handful of milliseconds — the generalized form of diagnostics/
    # snapshot_lab.py. This is the fast checkpoint an iterative loop wants.
    #
    # PORTABLE form (save_state(portable=True)): a raw uc context blob embeds
    # process-local pointers — restoring one in a different process SIGBUSes
    # (verified: same unicorn build, same CPU model, byte-identical config).
    # The portable form therefore enumerates architectural state explicitly
    # (general regs + A-profile banked regs + curated CP15 set, or M-profile
    # system regs) into plain python values, which is what disk persistence
    # (snapshot/persist.py) requires. Slightly slower; still ~ms.
    # ------------------------------------------------------------------

    # A-profile banked modes visited by the portable capture/restore dance.
    # "sys" (0x1f) shares sp/lr with usr and has no SPSR; fiq additionally
    # banks r8-r12.
    _ARM_BANKED_MODES = (
        (0x11, "fiq"), (0x12, "irq"), (0x13, "svc"),
        (0x17, "abt"), (0x1b, "und"), (0x1f, "sys"),
    )
    # Curated CP15 system registers, as (crn, crm, opc1, opc2) for
    # UC_ARM_REG_CP_REG. Covers the ARMv5/ARM926 MMU set PLUS the v6/v7 regs
    # that later A-profile CPU models (cortex-a*) implement — VBAR (relocated
    # vector base), TTBR1 + the LPAE memory-attribute regs, and the thread-ID
    # (TPIDR*) regs an RTOS uses. Reads/writes are per-register guarded, so a
    # model that doesn't implement one simply skips it (absent from the dict).
    # Restore order matters: SCTLR (the MMU enable) is written LAST so
    # translation state (TTBR/DACR/attrs) is in place before the M bit flips.
    _CP15_PORTABLE = (
        ("ttbr0",      (2, 0, 0, 0)),
        ("ttbr1",      (2, 0, 0, 1)),   # v6+
        ("ttbcr",      (2, 0, 0, 2)),
        ("dacr",       (3, 0, 0, 0)),
        ("dfsr",       (5, 0, 0, 0)),
        ("ifsr",       (5, 0, 0, 1)),
        ("dfar",       (6, 0, 0, 0)),
        ("ifar",       (6, 0, 0, 2)),   # v6+
        ("prrr_mair0", (10, 2, 0, 0)),  # PRRR / MAIR0 (v6+/v7)
        ("nmrr_mair1", (10, 2, 0, 1)),  # NMRR / MAIR1 (v6+/v7)
        ("vbar",       (12, 0, 0, 0)),  # v7: relocated exception vector base
        ("fcseidr",    (13, 0, 0, 0)),
        ("contextidr", (13, 0, 0, 1)),
        ("tpidrurw",   (13, 0, 0, 2)),  # v6+ user RW thread-id
        ("tpidruro",   (13, 0, 0, 3)),  # v6+ user RO thread-id
        ("tpidrprw",   (13, 0, 0, 4)),  # v6+ priv thread-id
        ("cpacr",      (1, 0, 0, 2)),   # coprocessor (VFP) access control
        ("sctlr",      (1, 0, 0, 0)),
    )
    # M-profile system registers (arm_const name suffixes).
    _M_PROFILE_SYSREGS = ("MSP", "PSP", "PRIMASK", "BASEPRI",
                          "FAULTMASK", "CONTROL")
    # VFP/NEON registers captured on FP-capable cores (guarded — a core
    # without VFP raises on the read and the reg is skipped). d0-d31 covers
    # s0-s31 (aliased low halves); FPSCR is the status/control word.
    _VFP_DREGS = tuple(f"UC_ARM_REG_D{i}" for i in range(32))
    _VFP_CTRL = ("FPSCR",)

    def can_snapshot(self) -> bool:
        return self._uc is not None

    def snapshot_is_fast(self) -> bool:
        return True

    def _is_arm_profile_a(self) -> bool:
        arch_str, mode_str, *_ = _ARCH_MAP.get(
            self.arch_name, ("arm", "thumb", True, False, 4))
        return arch_str == "arm" and mode_str != "thumb"

    def _is_arm_profile_m(self) -> bool:
        arch_str, mode_str, *_ = _ARCH_MAP.get(
            self.arch_name, ("arm", "thumb", True, False, 4))
        return arch_str == "arm" and mode_str == "thumb"

    def _capture_banked_regs(self) -> Dict[str, Dict[str, int]]:
        """A-profile: visit each banked mode via raw CPSR writes and read its
        sp/lr/spsr (+ r8-r12 for fiq). CPSR is always restored, even if a
        mode read fails."""
        uc = self._uc
        cpsr_id = arm_const.UC_ARM_REG_CPSR
        orig = uc.reg_read(cpsr_id)
        banked: Dict[str, Dict[str, int]] = {}
        try:
            for mode_bits, tag in self._ARM_BANKED_MODES:
                uc.reg_write(cpsr_id, (orig & ~0x1F) | mode_bits)
                entry = {"sp": uc.reg_read(arm_const.UC_ARM_REG_SP),
                         "lr": uc.reg_read(arm_const.UC_ARM_REG_LR)}
                if tag != "sys":  # sys/usr have no SPSR
                    entry["spsr"] = uc.reg_read(arm_const.UC_ARM_REG_SPSR)
                if tag == "fiq":
                    for i in range(8, 13):
                        entry[f"r{i}"] = uc.reg_read(
                            getattr(arm_const, f"UC_ARM_REG_R{i}"))
                banked[tag] = entry
        finally:
            uc.reg_write(cpsr_id, orig)
        return banked

    def _restore_banked_regs(self, banked: Dict[str, Dict[str, int]]) -> None:
        """Inverse of _capture_banked_regs. Caller restores the final CPSR."""
        uc = self._uc
        cpsr_id = arm_const.UC_ARM_REG_CPSR
        orig = uc.reg_read(cpsr_id)
        try:
            for mode_bits, tag in self._ARM_BANKED_MODES:
                entry = banked.get(tag)
                if not entry:
                    continue
                uc.reg_write(cpsr_id, (orig & ~0x1F) | mode_bits)
                uc.reg_write(arm_const.UC_ARM_REG_SP, entry["sp"])
                uc.reg_write(arm_const.UC_ARM_REG_LR, entry["lr"])
                if "spsr" in entry:
                    uc.reg_write(arm_const.UC_ARM_REG_SPSR, entry["spsr"])
                for i in range(8, 13):
                    if f"r{i}" in entry:
                        uc.reg_write(getattr(arm_const, f"UC_ARM_REG_R{i}"),
                                     entry[f"r{i}"])
        finally:
            uc.reg_write(cpsr_id, orig)

    def _capture_cp15(self) -> Dict[str, int]:
        """Read the curated CP15 set. Registers the CPU model doesn't
        implement are skipped (absent from the dict, skipped on restore)."""
        out: Dict[str, int] = {}
        for name, (crn, crm, opc1, opc2) in self._CP15_PORTABLE:
            try:
                out[name] = self._uc.reg_read(
                    arm_const.UC_ARM_REG_CP_REG,
                    (15, 0, 0, crn, crm, opc1, opc2))
            except Exception:  # noqa: BLE001 — not implemented on this model
                continue
        return out

    def _restore_cp15(self, cp15: Dict[str, int]) -> None:
        # _CP15_PORTABLE order is the restore order (SCTLR last).
        for name, (crn, crm, opc1, opc2) in self._CP15_PORTABLE:
            if name not in cp15:
                continue
            try:
                self._uc.reg_write(
                    arm_const.UC_ARM_REG_CP_REG,
                    (15, 0, 0, crn, crm, opc1, opc2, cp15[name]))
            except Exception:  # noqa: BLE001
                log.warning("restore_state: CP15 %s not writable on this "
                            "CPU model; skipped", name)

    def _capture_vfp(self) -> Dict[str, int]:
        """Read the VFP/NEON register file. On a core without VFP the reads
        raise and the register is skipped (absent from the dict), so this is
        a no-op on ARM926 and captures the full FP state on cortex-a*/FPU-M."""
        out: Dict[str, int] = {}
        for i, dname in enumerate(self._VFP_DREGS):
            rid = getattr(arm_const, dname, None)
            if rid is None:
                continue
            try:
                out[f"d{i}"] = self._uc.reg_read(rid)
            except Exception:  # noqa: BLE001 — no VFP on this model
                continue
        for suffix in self._VFP_CTRL:
            rid = getattr(arm_const, f"UC_ARM_REG_{suffix}", None)
            if rid is None:
                continue
            try:
                out[suffix.lower()] = self._uc.reg_read(rid)
            except Exception:  # noqa: BLE001
                continue
        return out

    def _restore_vfp(self, vfp: Dict[str, int]) -> None:
        for i, dname in enumerate(self._VFP_DREGS):
            key = f"d{i}"
            if key not in vfp:
                continue
            rid = getattr(arm_const, dname, None)
            if rid is None:
                continue
            try:
                self._uc.reg_write(rid, vfp[key])
            except Exception:  # noqa: BLE001
                log.warning("restore_state: VFP %s not writable; skipped", key)
        for suffix in self._VFP_CTRL:
            if suffix.lower() not in vfp:
                continue
            rid = getattr(arm_const, f"UC_ARM_REG_{suffix}", None)
            if rid is not None:
                try:
                    self._uc.reg_write(rid, vfp[suffix.lower()])
                except Exception:  # noqa: BLE001
                    pass

    def _capture_portable_regs(self) -> Dict[str, Any]:
        """Architectural state as plain python values — safe to pickle and
        restore in a different process (unlike a raw uc context blob)."""
        uc = self._uc
        state: Dict[str, Any] = {
            "regs": {name: uc.reg_read(rid)
                     for name, rid in self._reg_map.items()},
        }
        if self._is_arm_profile_a():
            state["banked"] = self._capture_banked_regs()
            state["cp15"] = self._capture_cp15()
            state["vfp"] = self._capture_vfp()
        elif self._is_arm_profile_m():
            sysregs: Dict[str, int] = {}
            for suffix in self._M_PROFILE_SYSREGS:
                rid = getattr(arm_const, f"UC_ARM_REG_{suffix}", None)
                if rid is None:
                    continue
                try:
                    sysregs[suffix.lower()] = uc.reg_read(rid)
                except Exception:  # noqa: BLE001
                    continue
            state["m_sysregs"] = sysregs
            state["vfp"] = self._capture_vfp()  # FPU-equipped M-profile
        else:
            # Non-ARM: the arch reg map covers the visible register file but
            # NOT hidden system state (x86 segment descriptors/MSRs, MIPS
            # cp0, ...). Good enough for flat-model targets; be honest here.
            log.warning("save_state(portable=True) on %s captures the "
                        "general register file only — hidden system state "
                        "is not yet enumerated for this arch", self.arch_name)
        return state

    def _restore_portable_regs(self, state: Dict[str, Any]) -> None:
        uc = self._uc
        regs: Dict[str, int] = state.get("regs", {})
        # System state first (CP15 translation regs before SCTLR, banked
        # modes before the final CPSR), then the general file with CPSR
        # first (mode/T bit context) and PC last.
        if "cp15" in state:
            self._restore_cp15(state["cp15"])
        if "vfp" in state:
            self._restore_vfp(state["vfp"])
        if "banked" in state:
            self._restore_banked_regs(state["banked"])
        for suffix_l, value in state.get("m_sysregs", {}).items():
            rid = getattr(arm_const, f"UC_ARM_REG_{suffix_l.upper()}", None)
            if rid is None:
                continue
            try:
                uc.reg_write(rid, value)
            except Exception:  # noqa: BLE001
                log.warning("restore_state: m-profile %s not writable; "
                            "skipped", suffix_l)
        ordered = sorted(regs,
                         key=lambda n: (0 if n == "cpsr" else
                                        2 if n == "pc" else 1))
        for name in ordered:
            try:
                uc.reg_write(self._reg_map[name], regs[name])
            except Exception:  # noqa: BLE001 — read-only alias on this model
                log.debug("restore_state: register %s not writable; skipped",
                          name)

    def _machine_fingerprint(self) -> Dict[str, Any]:
        """Identifies the machine config a snapshot was taken on: arch, CPU
        model, and the mapped memory layout. A portable snapshot restored onto
        a DIFFERENT config (wrong --emulator machine.yaml, different CPU model)
        would half-mutate before failing; comparing this fingerprint rejects
        the mismatch cleanly before any write."""
        return {
            "arch": self.arch_name,
            "cpu_model": getattr(self, "_cpu_model_name", None),
            "regions": sorted((base, end)
                              for (base, end, _p) in self._uc.mem_regions()),
        }

    def save_state(self, portable: bool = False) -> "Snapshot":
        from .hal_backend import Snapshot, SnapshotError
        if self._uc is None:
            raise SnapshotError(
                "UnicornBackend.save_state: engine not initialised "
                "(call init() first)")
        try:
            # bytes(): unicorn's mem_write requires bytes (rejects the
            # bytearray mem_read returns), so this conversion is load-bearing
            # on the restore path, not merely defensive.
            mem = [(base, bytes(self._uc.mem_read(base, end - base + 1)))
                   for (base, end, _perm) in self._uc.mem_regions()]
            if portable:
                data: Dict[str, Any] = {"portable": True,
                                        "fingerprint": self._machine_fingerprint(),
                                        **self._capture_portable_regs()}
            else:
                data = {"context": self._uc.context_save()}
            data["mem"] = mem
        except SnapshotError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SnapshotError(
                f"UnicornBackend.save_state failed: {exc!r}") from exc
        return Snapshot(backend_type=self.__class__.__name__,
                        version=self.SNAPSHOT_VERSION,
                        data=data)

    def restore_state(self, snap: "Snapshot") -> bool:
        from .hal_backend import log_snapshot_mismatch
        if snap.backend_type != self.__class__.__name__:
            log_snapshot_mismatch(self, snap, "backend_type")
            return False
        if snap.version != self.SNAPSHOT_VERSION:
            log_snapshot_mismatch(self, snap, "version")
            return False
        if self._uc is None:
            log.error("UnicornBackend.restore_state: engine not initialised")
            return False
        data = snap.data or {}
        # Validate the machine fingerprint BEFORE any write (validate-before-
        # mutate). A portable snapshot from a different arch/CPU/memory map
        # can't be applied coherently — reject it whole rather than half-write.
        fp = data.get("fingerprint")
        if fp is not None:
            current = self._machine_fingerprint()
            if fp != current:
                log.error("UnicornBackend.restore_state: snapshot machine "
                          "fingerprint %r != current %r; refusing (restore "
                          "with the same config the snapshot was taken on)",
                          fp, current)
                return False
        try:
            for base, blob in data.get("mem", []):
                self._uc.mem_write(base, blob)
            if data.get("portable"):
                self._restore_portable_regs(data)
            else:
                context = data.get("context")
                if context is not None:
                    self._uc.context_restore(context)
        except Exception as exc:  # noqa: BLE001
            log.error("UnicornBackend.restore_state failed: %r", exc)
            return False
        return True

    def read_memory(self, addr: int, size: int, num_words: int = 1,
                    raw: bool = False) -> Union[int, bytes]:
        total = size * num_words
        data = bytes(self._uc.mem_read(addr, total))
        if raw or num_words > 1:
            return data
        if size == 1:
            return data[0]
        order = "big" if self._is_be else "little"
        return int.from_bytes(data[:size], order)

    def write_memory(self, addr: int, size: int,
                     value: Union[int, bytes, bytearray],
                     num_words: int = 1, raw: bool = False) -> bool:
        if isinstance(value, (bytes, bytearray)):
            data = bytes(value)
        else:
            order = "big" if self._is_be else "little"
            data = value.to_bytes(size * num_words, order)
        try:
            self._uc.mem_write(addr, data)
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Registers
    # ------------------------------------------------------------------

    def read_register(self, register: str) -> int:
        uc_reg = self._reg_map.get(register.lower())
        if uc_reg is None:
            raise ValueError(f"Unknown register: {register!r}")
        return self._uc.reg_read(uc_reg)

    def write_register(self, register: str, value: int) -> None:
        uc_reg = self._reg_map.get(register.lower())
        if uc_reg is None:
            raise ValueError(f"Unknown register: {register!r}")
        self._uc.reg_write(uc_reg, value)

    # ------------------------------------------------------------------
    # Execution control
    # ------------------------------------------------------------------

    def set_breakpoint(self, addr: int, hardware: bool = False,
                       temporary: bool = False) -> int:
        bp_id = self._next_bp_id
        self._next_bp_id += 1
        # Store with Thumb bit cleared for comparison in _code_hook
        self._breakpoints[addr & 0xFFFFFFFE] = bp_id
        return bp_id

    def remove_breakpoint(self, bp_id: int) -> None:
        to_remove = [a for a, bid in self._breakpoints.items() if bid == bp_id]
        for addr in to_remove:
            del self._breakpoints[addr]

    def set_watchpoint(self, addr: int, write: bool = True,
                       read: bool = False, size: int = 4) -> int:
        """Install a per-address memory-access hook. Fires emu_stop when
        the firmware reads/writes the watched byte range."""
        if self._uc is None:
            raise RuntimeError("Call UnicornBackend.init() first")
        hook_type = 0
        if read:
            hook_type |= unicorn.UC_HOOK_MEM_READ
        if write:
            hook_type |= unicorn.UC_HOOK_MEM_WRITE
        if hook_type == 0:
            raise ValueError("watchpoint must have read or write enabled")

        bp_id = self._next_bp_id
        self._next_bp_id += 1

        def _watch_hook(uc, access, watch_addr, watch_size, value, user_data):
            # UC_HOOK_MEM_* already filters by the range we registered on,
            # so any call here is a hit.
            self._stopped = True
            self._bp_hit_addr = watch_addr
            uc.emu_stop()

        handle = self._uc.hook_add(
            hook_type, _watch_hook,
            begin=addr, end=addr + size - 1,
        )
        # Reuse _bp_hooks storage — (address, hook handle) is enough to
        # remove it later.
        self._bp_hooks[bp_id] = (addr, handle)
        return bp_id

    def remove_watchpoint(self, bp_id: int) -> None:
        entry = self._bp_hooks.pop(bp_id, None)
        if entry is None:
            return
        _, handle = entry
        if self._uc is not None:
            try:
                self._uc.hook_del(handle)
            except Exception:  # noqa: BLE001
                pass

    def cont(self, blocking: bool = True) -> None:
        if self._uc is None:
            raise RuntimeError("Call UnicornBackend.init() first")
        self._stopped = False
        self._bp_hit_addr = None
        until = (1 << (self._word_size * 8)) - 1
        # Loop over emu_start so an emu_stop triggered by inject_irq
        # from another thread doesn't bubble out to the dispatch loop.
        # We only return when a real breakpoint hook fires
        # (self._stopped sticks True) or stop() is called externally.
        # x86 async IRQ delivery (timer thread -> _pending_irqs) must NOT
        # call uc.emu_stop() cross-thread (deadlocks unicorn). Instead we
        # run x86 in bounded instruction chunks so this (dispatch) thread
        # returns from emu_start on its own and drains the queue between
        # chunks. Other arches keep the unbounded run (count=0).
        # x86 and A-profile arm (arm_vic) deliver async IRQs from a timer
        # thread via _pending_irqs; run those in bounded chunks so this
        # dispatch thread returns from emu_start on its own and drains the
        # queue WITHOUT a cross-thread emu_stop (which deadlocks unicorn).
        # ARM/x86 run in bounded chunks so this (dispatch) thread can
        # check for queued IRQs between chunks. Override via
        # HAL_IRQ_CHUNK env var (set to e.g. 50000 when debugging boot
        # where the cold-boot completes in fewer than 2M insns and you
        # need an earlier drain).
        import os as __os
        _chunk_env = __os.environ.get("HAL_IRQ_CHUNK")
        if _chunk_env:
            irq_chunk = int(_chunk_env, 0)
        elif self.arch_name in ("x86", "arm"):
            irq_chunk = 2_000_000
        else:
            irq_chunk = 0
        while True:
            # Drain any IRQs queued from another thread before
            # resuming — the synthetic exception frame setup mutates
            # PC/SP, only safe when emu_start is not running.
            while self._pending_irqs:
                self._apply_pending_irq(self._pending_irqs.pop(0))
            pc = self.read_register("pc")
            # Unicorn Thumb mode needs the LSB set on the start
            # address.
            start = (pc | 1) if self._is_thumb else pc
            try:
                self._uc.emu_start(start, until, timeout=0, count=irq_chunk)
            except unicorn.UcError as _uc_err:
                if self._stopped:
                    return  # stopped by breakpoint hook — normal
                # Diagnostic (HAL_LAST_PC): dump the block path leading into the fault.
                _lb = getattr(self, "_last_blocks", None)
                if _lb:
                    log.error("HAL_LAST_PC: last %d basic blocks before fault: %s",
                              len(_lb), " -> ".join("0x%08x" % p for p in _lb))
                    try:
                        _sp = self.read_register("sp") & 0xFFFFFFFF
                        _regs = {r: self.read_register(r) & 0xFFFFFFFF
                                 for r in ("r10", "r11", "lr")}
                        _stk = []
                        for _o in range(0, 24, 4):
                            try:
                                _stk.append(self._uc.mem_read(_sp + _o, 4))
                            except Exception:  # noqa: BLE001
                                _stk.append(b"\xff\xff\xff\xff")
                        _cur = 0
                        try:
                            _cp = self._uc.mem_read(0x202AD2FC, 4)
                            _cur = int.from_bytes(_cp, "little")
                            _cur = int.from_bytes(self._uc.mem_read(_cur, 4), "little")
                        except Exception:  # noqa: BLE001
                            pass
                        log.error("HAL_LAST_PC: sp=0x%08x r10=0x%08x r11=0x%08x lr=0x%08x curTCB=0x%08x "
                                  "stack=[%s]", _sp, _regs["r10"], _regs["r11"], _regs["lr"], _cur,
                                  " ".join("0x%08x" % int.from_bytes(w, "little") for w in _stk))
                    except Exception:  # noqa: BLE001
                        pass
                # x86 flat-segment recovery: _intr_hook decoded a far
                # control transfer and stashed the resume EIP. Re-enter
                # emu_start at it (read_register("pc") already returns it).
                if getattr(self, "_x86_resume_eip", None) is not None:
                    self._x86_resume_eip = None
                    continue
                # Bad-call recovery: an indirect call through an unsatisfiable
                # _func_ hook landed in non-code -> return to lr (the wrapper
                # set it with `mov lr, pc`). Capped per fault PC.
                if (self._recover_bad_calls
                        and _uc_err.errno in (
                            unicorn.UC_ERR_INSN_INVALID,
                            unicorn.UC_ERR_FETCH_UNMAPPED,
                            unicorn.UC_ERR_FETCH_PROT)):
                    fault_pc = self.read_register("pc")
                    lr_reg = self._reg_map.get("lr")
                    lr = (self._uc.reg_read(lr_reg) & ~1) if lr_reg else 0
                    self._bad_call_recover[fault_pc] = (
                        self._bad_call_recover.get(fault_pc, 0) + 1)
                    n = self._bad_call_recover[fault_pc]
                    # Spin detection: if the same (fault_pc, lr) keeps
                    # appearing, the boot is wedged in an uninitialised-
                    # dispatch loop. After 20 identical recoveries, escape
                    # by walking the stack one frame up.
                    last_pair = getattr(self, "_bad_call_last_pair", None)
                    if last_pair == (fault_pc, lr):
                        self._bad_call_same_count = (
                            getattr(self, "_bad_call_same_count", 0) + 1)
                    else:
                        self._bad_call_same_count = 1
                        self._bad_call_last_pair = (fault_pc, lr)
                    spinning = self._bad_call_same_count >= 20
                    if (lr and lr != fault_pc and n <= 100 and not spinning):
                        # Successful bad-call recovery: log at WARNING.
                        # Unrecoverable cases (spinning, cannot recover)
                        # below still log at ERROR.
                        log.warning("UnicornBackend: bad call at 0x%08x -> "
                                    "return lr=0x%08x  (recovery #%d)",
                                    fault_pc, lr, n)
                        self.write_register("pc", lr)
                        continue
                    if spinning:
                        log.error("UnicornBackend: spin detected at "
                                  "fault=0x%08x lr=0x%08x (n=%d) -- "
                                  "unwinding one frame", fault_pc, lr, n)
                        # Reset spin counter so the next iter (after unwind)
                        # doesn't re-trigger immediately.
                        self._bad_call_same_count = 0
                        self._bad_call_last_pair = None
                        # Fall through to SP-peek (below) to find a deeper
                        # return address that's NOT lr.
                    # SP-scan recovery: either lr=0, or we're spinning at the
                    # same lr (cap exceeded). Walk the stack for a saved
                    # return address that ISN'T the current lr (so we unwind
                    # past the spinning frame). We do NOT advance SP --
                    # earlier versions did, and the cumulative SP creep ended
                    # up pointing into unmapped/MMIO memory after a few dozen
                    # unwinds. Leave SP alone; the function we return to
                    # will manage its own frame via its prologue/epilogue.
                    try:
                        sp = self.read_register("sp")
                        # sanity: if SP is already garbage, refuse SP-peek
                        # rather than reading 0s from a lazy-mapped MMIO page.
                        # Accepted ranges: lowram, sdram, sdram_bank1, and the
                        # high_stack_ram window the target PLC task allocator
                        # uses (the target's auto-memory YAML maps
                        # 0xffff0000-0xffffffff as real RAM specifically so
                        # high-SP stacks work).
                        if not (0x00000000 <= sp < 0x10000000
                                or 0x20000000 <= sp < 0x24000000
                                or 0xffff0000 <= sp < 0x100000000):
                            log.error("UnicornBackend: SP=0x%08x is outside "
                                      "valid stack ranges; skipping SP-peek",
                                      sp)
                            raise RuntimeError("sp out of range")
                        for ofs in range(0, 64 * 4, 4):
                            word = int.from_bytes(
                                self._uc.mem_read(sp + ofs, 4), "little")
                            # accept word if it points into SDRAM code region
                            # and isn't the faulting PC or current lr
                            if (0x20000000 <= word < 0x24000000
                                    and word != fault_pc
                                    and word != fault_pc + 1
                                    and word != lr
                                    and (word & 1) == 0):  # ARM (not Thumb)
                                log.error("UnicornBackend: stack-unwind at "
                                          "0x%08x lr=0x%08x sp=0x%08x; peek "
                                          "[sp+0x%x] = 0x%08x -> "
                                          "return there (recovery #%d)",
                                          fault_pc, lr, sp, ofs, word,
                                          self._bad_call_recover[fault_pc])
                                self.write_register("pc", word)
                                break
                        else:
                            raise RuntimeError("no return-addr in 64 words")
                        continue
                    except Exception as _e:
                        pass
                    log.error("UnicornBackend: cannot recover from 0x%08x "
                              "lr=0x%08x (n=%d)",
                              fault_pc, lr,
                              self._bad_call_recover[fault_pc])
                    # Last-ditch boot rescue: if HAL_BOOT_RESCUE_PC is
                    # set, jump there instead of dying. Intended use:
                    # a planted `b .` idle loop, so the dispatch thread
                    # stays alive and the TimerModel can drain pending
                    # IRQs into the configured ArmVicController.isr_addr.
                    import os as __os
                    _rescue = __os.environ.get("HAL_BOOT_RESCUE_PC")
                    if _rescue:
                        try:
                            rescue_pc = int(_rescue, 0)
                            log.error("UnicornBackend: BOOT RESCUE -> "
                                      "jumping PC to 0x%08x (idle loop)",
                                      rescue_pc)
                            self.write_register("pc", rescue_pc)
                            # Also unmask IRQs (clear CPSR.I bit) so
                            # queued TimerModel ticks can actually be
                            # delivered.  The reset stub left CPSR.I=1
                            # and we never reached the kernel code that
                            # normally clears it.
                            if self.arch_name == "arm":
                                try:
                                    cpsr = self.read_register("cpsr")
                                    # Force CPSR -> SVC mode (0x13), I=0,
                                    # F=0, T=0. Keep the upper condition
                                    # flags (NZCV etc.).
                                    new_cpsr = (cpsr & 0xfffffe00) | 0x13
                                    self.write_register("cpsr", new_cpsr)
                                    log.error("UnicornBackend: reset CPSR "
                                              "(was 0x%x, now 0x%x: SVC+I0+F0)",
                                              cpsr, new_cpsr)
                                except Exception as _e:
                                    log.error("UnicornBackend: CPSR "
                                              "reset failed: %s", _e)
                            continue
                        except Exception as _e:
                            log.error("UnicornBackend: rescue failed: %s", _e)
                # emu_stop without a breakpoint hook firing: either
                # inject_irq queued an IRQ on another thread, or
                # something asked us to stop. If the former, loop and
                # apply the IRQ. Otherwise honour the stop.
                if not self._pending_irqs:
                    # Print PC + LR so the user can find where boot died.
                    try:
                        fpc = self.read_register("pc")
                        lr_reg = self._reg_map.get("lr")
                        flr = (self._uc.reg_read(lr_reg) & ~1) if lr_reg else 0
                        log.error("UnicornBackend: UcError %s at PC=0x%08x lr=0x%08x",
                                  _uc_err, fpc, flr)
                    except Exception:
                        pass
                    raise
                # fall through to drain queue + re-enter emu_start
                continue
            # emu_start returned without UcError: same logic as
            # above — drain pending or honour external stop. The x86
            # flat-segment recovery stops cleanly (emu_stop in the INTR
            # hook), so check the resume flag here too.
            if getattr(self, "_x86_resume_eip", None) is not None:
                self._x86_resume_eip = None
                continue
            if self._pending_irqs:
                continue
            # x86 runs in bounded chunks: a clean return means the chunk's
            # instruction count was reached, NOT that emulation is done.
            # Keep running unless a breakpoint/stop() set self._stopped.
            if irq_chunk and not self._stopped:
                # Deterministic system-clock tick: every _det_period completed chunks, queue the
                # clock IRQ (instruction-count-paced, not wall-clock). Drained at the top of the
                # next iteration like any pending IRQ.
                if self._det_irq is not None:
                    self._det_chunks += 1
                    if self._det_chunks % self._det_period == 0:
                        self._pending_irqs.append(self._det_irq)
                continue
            return

    def continue_past_breakpoint(self) -> None:
        """Resume after an observe-only (non-intercept) bp_handler.

        The breakpoint sits on the intercepted function's entry; to run the
        real function we must let that one instruction execute without the
        bp immediately re-stopping us. Arm a one-shot skip for the last-hit
        address, then continue until the next breakpoint. The bp re-arms
        automatically for subsequent calls."""
        self._skip_bp_once = self._bp_hit_addr
        self.cont()

    def stop(self) -> None:
        self._stopped = True
        if self._uc is not None:
            self._uc.emu_stop()

    def step(self) -> None:
        if self._uc is None:
            raise RuntimeError("Call UnicornBackend.init() first")
        pc = self.read_register("pc")
        start = (pc | 1) if self._is_thumb else pc
        until = (1 << (self._word_size * 8)) - 1
        self._uc.emu_start(start, until, timeout=0, count=1)

    # ------------------------------------------------------------------
    # IRQ injection — not supported in-process; log warning
    # ------------------------------------------------------------------

    # ARM-v7M exception-return magic values. When the ISR issues `bx lr`
    # with LR holding one of these, cortex-m normally pops the exception
    # frame and resumes. Unicorn doesn't model that transition, so we
    # catch the invalid fetch and unwind manually.
    _EXC_RETURN_THREAD_MSP = 0xFFFFFFF9
    _EXC_RETURN_MASK = 0xFFFFFFF0
    _EXC_RETURN_MAGIC = 0xFFFFFFF0  # any PC matching this top nibble is
                                     # an exception-return attempt

    # The avatar2/QEMU path implements these by writing to the halucinator-irq
    # controller MMIO region. Unicorn doesn't model a NVIC/GIC, so IRQ
    # delivery goes through inject_irq() / IrqController.trigger() instead.
    # Peripheral models call these defensively to deassert lines that were
    # never asserted via MMIO; stub them so peripheral_server.irq_clear_bp()
    # etc. don't AttributeError (e.g. UTTYModel clearing its rx line).
    def irq_set_bp(self, irq_num: int = 1) -> None:
        return None

    def irq_clear_bp(self, irq_num: int = 1) -> None:
        return None

    def irq_enable_bp(self, irq_num: int = 1) -> None:
        return None

    def inject_irq(self, irq_num: int) -> None:
        """Deliver an external IRQ.

        Cortex-M3 / ARMv7-A fast-path: queue the IRQ for the dispatch
        loop, then call ``emu_stop`` to break out of any in-flight
        ``emu_start``. cont() drains the queue (synthesises the
        exception entry on the main stack, sets banked LR_irq, jumps
        PC to the architectural IRQ vector) immediately before
        re-entering ``emu_start`` so all CPU-state mutation happens
        single-threaded. Skips controller-MMIO writes — unicorn
        doesn't model the NVIC or GIC.

        For other arches, fall through to HalBackend.inject_irq, which
        routes through the configured IrqController (CP0 Cause for
        MIPS, OpenPIC IPIDR for PPC). MMIO writes go through unicorn's
        normal write_memory and the next cont() will take the
        exception when the firmware unmasks.
        """
        if self.arch_name not in ("cortex-m3", "arm", "arm64", "mips",
                                   "powerpc", "powerpc:MPC8XX", "ppc64"):
            super().inject_irq(irq_num)
            return
        if self._uc is None:
            raise RuntimeError("Call UnicornBackend.init() first")
        # Deterministic-tick mode: the system clock IRQ is driven from instruction count in
        # cont(), so ignore the wall-clock timer thread's injections of that same IRQ (avoid
        # double-ticking). Other IRQs still deliver normally.
        if self._det_irq is not None and int(irq_num) == self._det_irq:
            return
        # A-profile arm with a *synthesising* controller (ArmVicController,
        # the ARM mirror of X86PicController): the controller's trigger()
        # owns the queue — it appends to _pending_irqs from the timer
        # thread, and cont() drains it via _apply_pending_irq -> deliver()
        # in bounded chunks. There is no controller MMIO to write (the SoC
        # VIC isn't modelled), so route exactly like x86 and return: just
        # trigger (queue), do NOT manually append or cross-thread emu_stop.
        if self.arch_name == "arm":
            ctrl = getattr(self, "_irq_controller", None)
            if ctrl is not None and hasattr(ctrl, "deliver"):
                ctrl.trigger(self, irq_num)
                return
        # On arm/arm64, the IrqController MMIO write (GICD_ISPENDR
        # for arm/arm64, NVIC_ISPR for cortex-m3) is still useful —
        # firmware that polls those registers should see the bit
        # set. Cortex-m3's _apply_pending_irq always synthesises the
        # exception, so skip the controller MMIO there. For arm /
        # arm64 we emit both: real GIC writes happen through the
        # controller, and the synthetic exception entry fires from
        # cont().
        if self.arch_name in ("arm", "arm64", "mips",
                               "powerpc", "powerpc:MPC8XX", "ppc64"):
            ctrl = getattr(self, "_irq_controller", None)
            if ctrl is None:
                from halucinator.backends.irq import IrqConfigError
                raise IrqConfigError(
                    f"UnicornBackend(arch={self.arch_name!r}) has no "
                    "interrupt controller configured. Set "
                    "machine.interrupt_controller in the YAML or call "
                    "set_irq_controller() before inject_irq()."
                )
            try:
                ctrl.trigger(self, irq_num)
            except Exception as exc:  # noqa: BLE001
                # MIPS: the controller does an RMW on CP0 'cause'
                # which unicorn doesn't expose. Swallow the
                # register-not-found error (the synthetic entry
                # below still delivers) but let bounds and other
                # config errors surface.
                if self.arch_name == "mips" and "cause" in str(exc):
                    pass
                else:
                    raise
        # Cross-thread safe: list.append() + emu_stop are atomic from
        # Python's perspective. The dispatch thread will see the
        # pending entry on its next cont() call.
        self._pending_irqs.append(int(irq_num))
        try:
            self._uc.emu_stop()
        except Exception:  # noqa: BLE001 — uc raises if not running
            pass

    def _apply_pending_irq(self, irq_num: int) -> None:
        """Set up the synthetic exception entry for a pended IRQ.
        Must run on the dispatch thread (between emu_start chunks)
        — Unicorn isn't safe against PC/SP writes mid-run."""
        if self._uc is None:
            return
        if self.arch_name == "arm":
            # A-profile ARM IRQ delivery. Preferred path: the refactored
            # ExceptionDeliverer + DeliveryPlan (set via main._wire_irq).
            # It subsumes both the ArmVicController.deliver synth path and
            # the built-in _apply_pending_irq_armv7a GIC path (proven
            # equivalent in test_arm_deliverer_equivalence.py). Only the
            # FRAME/TRAMPOLINE models are handled by ArmExceptionDeliverer;
            # SHADOW (ghidra) and the unconfigured case fall through to the
            # legacy logic below.
            from halucinator.backends.irq.delivery import DeliveryModel
            deliverer = getattr(self, "_exception_deliverer", None)
            plan = getattr(self, "_delivery_plan", None)
            if (deliverer is not None and plan is not None
                    and plan.model in (DeliveryModel.FRAME,
                                       DeliveryModel.TRAMPOLINE)):
                deliverer.deliver(self, irq_num, plan)
                return
            # Legacy: if the configured controller synthesises the
            # exception itself (ArmVicController), route through it;
            # otherwise the built-in ARMv7-A/GIC entry (VBAR+0x18 +
            # GICC_IAR shadow).
            ctrl = getattr(self, "_irq_controller", None)
            if ctrl is not None and hasattr(ctrl, "deliver"):
                # deliver() returns False when CPSR.I masks IRQs — like the
                # x86_pic path we simply drop that tick (the next periodic
                # tick lands once the firmware re-enables IRQs). We do NOT
                # re-queue, which would busy-spin this chunk re-applying a
                # tick that can never enter.
                ctrl.deliver(self, irq_num)
                return
            self._apply_pending_irq_armv7a(irq_num)
            return
        if self.arch_name == "arm64":
            self._apply_pending_irq_arm64(irq_num)
            return
        if self.arch_name == "mips":
            self._apply_pending_irq_mips(irq_num)
            return
        if self.arch_name in ("powerpc", "powerpc:MPC8XX", "ppc64"):
            self._apply_pending_irq_ppc(irq_num)
            return
        if self.arch_name == "x86":
            # x86 PC interrupt delivery is owned entirely by the
            # configured X86PicController (backends/irq/x86_pic.py): it
            # synthesises the CPU interrupt frame and vectors to the
            # VxWorks interrupt stub. This runs on the dispatch thread
            # (here), so it is safe to mutate EIP/ESP.
            ctrl = getattr(self, "_irq_controller", None)
            if ctrl is not None and hasattr(ctrl, "deliver"):
                ctrl.deliver(self)
            else:
                log.warning("inject_irq(%d): x86 has no X86PicController "
                            "configured; tick dropped", irq_num)
            return

        # Vector table offset: caller plumbs it in via set_vtor(); fall
        # back to 0 for backward compatibility.
        vtor = getattr(self, "_vtor", 0)
        isr_slot = vtor + (16 + irq_num) * 4
        isr_addr = 0
        try:
            isr_addr = int.from_bytes(
                self._uc.mem_read(isr_slot, 4), "little"
            )
        except Exception:  # noqa: BLE001 — Unicorn raises UcError here
            pass
        if not isr_addr:
            log.warning("inject_irq(%d): vector table slot 0x%x is zero or "
                        "unmapped; no handler installed", irq_num, isr_slot)
            return

        # Push the 8-word exception frame.
        regs = {name: self.read_register(name) for name in
                ("r0", "r1", "r2", "r3", "r12", "lr", "pc", "cpsr")}
        sp = self.read_register("sp") - 32
        frame = (regs["r0"], regs["r1"], regs["r2"], regs["r3"],
                 regs["r12"], regs["lr"], regs["pc"], regs["cpsr"])
        import struct
        self._uc.mem_write(sp, struct.pack("<8I", *frame))
        self.write_register("sp", sp)
        self.write_register("lr", self._EXC_RETURN_THREAD_MSP)
        self.write_register("pc", isr_addr & ~1)  # Thumb bit goes in CPSR.T
        log.info("inject_irq(%d): entering ISR @ 0x%x (vector 0x%x)",
                 irq_num, isr_addr, isr_slot)

    def set_vtor(self, vtor: int) -> None:
        """Remember the vector-table base so inject_irq can find ISRs."""
        self._vtor = vtor

    # ARMv7-A CPSR mode bits.
    _ARM_MODE_USER = 0x10
    _ARM_MODE_FIQ  = 0x11
    _ARM_MODE_IRQ  = 0x12
    _ARM_MODE_SVC  = 0x13
    _ARM_MODE_ABT  = 0x17
    _ARM_MODE_UND  = 0x1B
    _ARM_MODE_SYS  = 0x1F
    _ARM_MODE_MASK = 0x1F
    _ARM_CPSR_I    = 0x80   # IRQ mask
    _ARM_CPSR_T    = 0x20   # Thumb

    def _apply_pending_irq_armv7a(self, irq_num: int) -> None:
        """Synthesise an ARMv7-A IRQ exception entry (legacy GIC path).

        Thin wrapper over the shared ``ArmExceptionDeliverer``: builds a
        FRAME ``DeliveryPlan`` from the vector base (``_vtor``) and the
        configured GIC's ``gicc_base`` (for the GICC_IAR shadow), then
        delegates. The only behaviour this wrapper adds over the deliverer
        is the legacy *masked-IRQ re-queue*: when delivery is suppressed
        (CPSR.I=1) it re-queues the tick and stops the run so the firmware
        can unmask, rather than dropping it (the ArmVicController path
        drops). That policy difference is intentionally preserved here.
        """
        from halucinator.backends.irq.delivery import (
            ArmExceptionDeliverer, DeliveryModel, DeliveryPlan)
        ctrl = getattr(self, "_irq_controller", None)
        gicc_base = getattr(ctrl, "gicc_base", None) if ctrl else None
        vbar = getattr(self, "_vtor", 0)
        plan = DeliveryPlan(model=DeliveryModel.FRAME, vector_base=vbar,
                            gicc_base=gicc_base)
        delivered = ArmExceptionDeliverer().deliver(self, irq_num, plan)
        if not delivered:
            # IRQs masked — re-queue and let the firmware unmask itself;
            # otherwise we'd nest exceptions.
            self._pending_irqs.insert(0, irq_num)
            self._uc.emu_stop()
            return
        log.info("inject_irq(%d): ARMv7-A entry @ 0x%x", irq_num, vbar + 0x18)

    def _resolve_delivery_plan(self, build_legacy):
        """Return the attached DeliveryPlan (new `irq_delivery` config) or,
        when none was set, a plan built from the legacy controller fields
        via ``build_legacy(ctrl)``. Centralises the new-vs-legacy choice so
        each per-arch wrapper stays a one-liner."""
        plan = getattr(self, "_delivery_plan", None)
        if plan is not None:
            return plan
        ctrl = getattr(self, "_irq_controller", None)
        return build_legacy(ctrl)

    def _apply_pending_irq_arm64(self, irq_num: int) -> None:
        """AArch64 IRQ entry — thin wrapper over Arm64ExceptionDeliverer."""
        from halucinator.backends.irq.delivery import (
            Arm64ExceptionDeliverer, DeliveryModel, DeliveryPlan)

        def _legacy(ctrl):
            simple = getattr(ctrl, "irq_simple_entry", None) if ctrl else None
            return DeliveryPlan(
                model=(DeliveryModel.TRAMPOLINE if simple is not None
                       else DeliveryModel.FRAME),
                vector_base=getattr(self, "_vtor", 0),
                trampoline=simple,
                gicc_base=getattr(ctrl, "gicc_base", None) if ctrl else None,
            )
        Arm64ExceptionDeliverer().deliver(self, irq_num,
                                          self._resolve_delivery_plan(_legacy))

    def _apply_pending_irq_mips(self, irq_num: int) -> None:
        """MIPS IRQ delivery — thin wrapper over ShadowExceptionDeliverer.

        Unicorn's MIPS model doesn't take CP0 exceptions reliably, so the
        shadow deliverer writes the firmware's post-ack globals directly."""
        from halucinator.backends.irq.delivery import (
            DeliveryModel, DeliveryPlan, ShadowExceptionDeliverer)

        def _legacy(ctrl):
            return DeliveryPlan(
                model=DeliveryModel.SHADOW,
                irq_fired_addr=getattr(ctrl, "irq_fired_addr", None) if ctrl else None,
                irq_number_addr=getattr(ctrl, "irq_number_addr", None) if ctrl else None,
            )
        ShadowExceptionDeliverer().deliver(self, irq_num,
                                           self._resolve_delivery_plan(_legacy))

    def _apply_pending_irq_ppc(self, irq_num: int) -> None:
        """PowerPC IRQ delivery — thin wrapper over ShadowExceptionDeliverer
        (same SHADOW pattern as MIPS; Unicorn doesn't model the OpenPIC /
        SRR0/SRR1 entry reliably for our use-case)."""
        from halucinator.backends.irq.delivery import (
            DeliveryModel, DeliveryPlan, ShadowExceptionDeliverer)

        def _legacy(ctrl):
            return DeliveryPlan(
                model=DeliveryModel.SHADOW,
                irq_fired_addr=getattr(ctrl, "irq_fired_addr", None) if ctrl else None,
                irq_number_addr=getattr(ctrl, "irq_number_addr", None) if ctrl else None,
            )
        ShadowExceptionDeliverer().deliver(self, irq_num,
                                           self._resolve_delivery_plan(_legacy))

    def _maybe_handle_exc_return(self, addr: int) -> bool:
        """Called from the invalid-fetch hook. If the fetch address looks
        like an EXC_RETURN magic value, pop the exception frame and
        restore pre-interrupt state. Returns True when handled."""
        if self.arch_name != "cortex-m3":
            return False
        if (addr & self._EXC_RETURN_MASK) != self._EXC_RETURN_MAGIC:
            return False
        import struct
        sp = self.read_register("sp")
        try:
            frame = struct.unpack("<8I", bytes(self._uc.mem_read(sp, 32)))
        except Exception:
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
        log.info("exc_return: popped frame, resuming at 0x%x", frame[6])
        # Unicorn needs to restart from the restored PC; stop the current
        # emu_start so our dispatch loop re-issues cont() at the new PC.
        self._uc.emu_stop()
        return True

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        if self._uc is not None:
            try:
                self._uc.emu_stop()
            except Exception:
                pass
            self._uc = None
