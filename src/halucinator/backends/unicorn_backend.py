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

from halucinator import hal_log
from .hal_backend import ABI_MIXINS, ARM32HalMixin, ARMHalMixin, HalBackend, MemoryRegion
from .irq.in_process import InProcessIrqMixin

log = logging.getLogger(__name__)
# Operator-facing messages (knob accepted / ignored) go here: the shipped
# logging.cfg leaves this module's logger inheriting root=ERROR, so log.info /
# log.warning are discarded by default -- a knob silently doing nothing is
# exactly what the user needs told. Same split the irq/ modules use.
hlog = hal_log.getHalLogger()

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

class UnicornBackend(InProcessIrqMixin, ARMHalMixin, HalBackend):
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
        # Set by _maybe_handle_exc_return: a Cortex-M exception return redirected
        # PC and emu_stop'd to force a restart at the restored PC. cont() must
        # treat that internal stop as "resume", NOT as a breakpoint/external
        # stop — otherwise it hands control back to the dispatch loop parked on
        # the restored PC (see cont() for why that livelocks / prematurely exits).
        self._exc_return_pending: bool = False
        self._breakpoints: Dict[int, int] = {}   # addr → bp_id
        # Fast-breakpoint mode (opt-in HAL_FAST_BP=1): instead of ONE global
        # per-instruction UC_HOOK_CODE that checks every PC against the
        # breakpoint set (a Python callback on every instruction, which
        # dominates runtime for compute-heavy firmware), install one
        # RANGE-BOUNDED UC_HOOK_CODE per breakpoint address. Unicorn filters the
        # hook range in C at translate time, so basic blocks containing no
        # breakpoint run at full JIT speed and never enter Python. Arch-agnostic
        # and off by default -- eligibility is finalised in init() (it is only
        # safe when no per-instruction feature LIVES INSIDE _code_hook, i.e. the
        # RAM-spin breaker and the non-MMIO loop-recover are both off).
        # Single `import os as _os` for the whole __init__ env-knob block: the
        # later re-imports in this method are harmless, but Python's local-
        # binding rule needs `_os` bound before its first use here.
        import os as _os
        self._fast_bp: bool = _os.environ.get("HAL_FAST_BP") == "1"
        self._fast_bp_active: bool = False
        self._fast_bp_warned: bool = False
        self._per_bp_hooks: Dict[int, Any] = {}   # addr → unicorn hook handle
        # RAM-flag spin breaker (opt-in, see _code_hook / _break_ram_spin).
        # Detect a spin by DISTINCT-PC count over a window: a tight loop (even
        # one spanning a function call) touches few distinct PCs, while real
        # progress touches many. Catches call-based `while(check())` spins the
        # old contiguous-window detector missed.
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
        # Cortex-M `wfe` executed as a no-op (see _insn_invalid_hook).
        self._wfe_skipped = 0
        # Supervisor-call diagnostics (see _maybe_handle_cortexm_svc).
        self._svc_count = 0
        self._svc_trace_n = int(_os.environ.get("HAL_SVC_TRACE", "0"), 0)
        _probe = _os.environ.get("HAL_SVC_TRACE_PROBE")
        self._svc_trace_probe = int(_probe, 0) if _probe else None
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
        # In-process IRQ state: the cross-thread pending-IRQ queue and the
        # HAL_DET_TICK deterministic-tick config (see InProcessIrqMixin).
        self._init_in_process_irq()
        # x86: when _intr_hook resolves a far control transfer (#GP from a
        # missing GDT), it stashes the resume EIP here so cont() re-enters
        # emu_start instead of aborting on the UcError. None when idle.
        self._x86_resume_eip: Optional[int] = None

        # Opt-in: skip an unhandled SVC instruction (advance past it and
        # zero r0) instead of aborting. Used to
        # tolerate fuzz-harness hypercalls baked into instrumented binaries
        # (e.g. P2IM's aflCall `svc #0x3f`).
        self.skip_svc: bool = False
        # M-profile SP banking done by hand, for firmware that has dropped
        # privilege. See _apply_cortex_m_fallback / _maybe_handle_exc_return.
        self._m_manual_bank: bool = False
        self._m_spsel: bool = False
        self._m_saved_msp = None

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
        self._bind_abi(arch)

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
            # Default Cortex-M3, but allow pinning a richer M-profile core via
            # HAL_CORTEXM_CPU_MODEL=UC_CPU_ARM_CORTEX_M4 (adds the DSP extension
            # — e.g. smulbb — that M4 firmware like the STM32WB uses) or _M7/_M33.
            import os as _os
            _m_name = _os.environ.get("HAL_CORTEXM_CPU_MODEL",
                                      "UC_CPU_ARM_CORTEX_M3")
            # Resolve by name like the A-profile HAL_ARM_CPU_MODEL lever, and
            # warn instead of silently falling back: a typo'd or non-CPU-model
            # constant would otherwise leave the user wondering why their M4
            # DSP instruction is still undefined. Only UC_CPU_ARM_* names are
            # accepted so an unrelated constant can't reach ctl_set_cpu_model.
            _m_model = (getattr(arm_const, _m_name, None)
                        if _m_name.startswith("UC_CPU_ARM_") else None)
            if _m_model is None:
                hlog.warning("UnicornBackend: unknown HAL_CORTEXM_CPU_MODEL=%r;"
                             " using UC_CPU_ARM_CORTEX_M3", _m_name)
                _m_model = arm_const.UC_CPU_ARM_CORTEX_M3
            try:
                self._uc.ctl_set_cpu_model(_m_model)
                if _m_name != "UC_CPU_ARM_CORTEX_M3":
                    hlog.info("UnicornBackend: Cortex-M CPU model = %s",
                              _m_name)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "UnicornBackend: ctl_set_cpu_model(%s) failed (%s)"
                    " — kernel boot may UC_ERR_INSN_INVALID", _m_name, exc,
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
            # SCB->ICSR (0xE000ED04): a write with PENDSVSET (bit28) requests a
            # PendSV — the RTOS context switch. Nothing else models the NVIC, so
            # watch that write and queue PendSV (exception 14, as irq -2).
            self._pendsv_pending = False
            # PC parked on the `str ICSR,PENDSVSET` store by emu_stop (see below).
            self._pendsv_store_parked = False
            # True while cont() single-steps to retire the parked store, so the
            # store's own re-write does not re-park (which would abort the step
            # and pin PC on the store forever).
            self._pendsv_stepping = False

            def _icsr_write(uc, access, addr, size, value, ud):  # noqa: ANN001
                if getattr(self, "_pendsv_stepping", False):
                    return                            # retiring the parked store
                if value & (1 << 28):                 # PENDSVSET
                    already = self._pendsv_pending
                    self._pendsv_pending = True
                    # A PendSV requested from THREAD mode (no exception in
                    # flight) has nothing to tail-chain its delivery off — the
                    # exc_return path only pends it when LEAVING a handler. This
                    # is how an RTOS starts its first switch (Zephyr's arch_swap
                    # sets PENDSVSET, unmasks, `isb`, and expects to be preempted
                    # immediately). Real hardware takes the PendSV at the next
                    # instruction boundary; mirror that by breaking out of
                    # emu_start so cont() synthesises the entry between chunks
                    # (PC/SP mutation is only safe there). emu_stop leaves PC
                    # parked on this store, so flag it for cont() to retire first
                    # (else the resumed thread re-writes PENDSVSET and ping-pongs
                    # forever). Break only the first time — the already-pending
                    # guard lets a deferred re-entry's store complete. From
                    # HANDLER mode we do NOT break: the existing exc_return
                    # tail-chain delivers it once the CPU drops back to thread.
                    if not already:
                        try:
                            ipsr = (uc.reg_read(unicorn.arm_const.UC_ARM_REG_IPSR)
                                    & 0x1FF)
                        except Exception:  # noqa: BLE001
                            ipsr = 0
                        if ipsr == 0:                 # thread mode
                            self._pendsv_store_parked = True
                            try:
                                uc.emu_stop()
                            except Exception:  # noqa: BLE001
                                pass
                if value & (1 << 27):                 # PENDSVCLR
                    self._pendsv_pending = False
            self._uc.hook_add(unicorn.UC_HOOK_MEM_WRITE, _icsr_write,
                              begin=0xE000ED04, end=0xE000ED07)

        # Breakpoint detection. Default: ONE global per-instruction UC_HOOK_CODE
        # checks every PC against the breakpoint set. With HAL_FAST_BP=1 and no
        # per-instruction feature living inside _code_hook (the RAM-spin breaker
        # and the non-MMIO loop-recover, both opt-in and off by default), install
        # one range-bounded hook per breakpoint instead -- blocks with no
        # breakpoint then run at full JIT speed (see set_breakpoint / __init__).
        self._fast_bp_active = (
            self._fast_bp
            and not self._break_ram_spins
            and not getattr(self, "auto_recover_loops", False))
        if self._fast_bp_active:
            for _bp_addr in list(self._breakpoints):
                self._install_bp_hook(_bp_addr)
            hlog.info("UnicornBackend: HAL_FAST_BP -- per-address breakpoint "
                      "hooks (no global per-instruction code hook)")
        else:
            if self._fast_bp:
                hlog.warning("UnicornBackend: HAL_FAST_BP ignored -- a per-"
                             "instruction _code_hook feature is active "
                             "(HAL_BREAK_RAM_SPINS/auto_recover_loops)")
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

        # Cortex-M `wfe` is rejected by unicorn's M-profile decoder. UC_HOOK_INTR
        # does NOT fire for an undefined instruction, so the recovery has to hang
        # off the dedicated invalid-instruction hook. See _insn_invalid_hook.
        if self.arch_name == "cortex-m3":
            self._uc.hook_add(unicorn.UC_HOOK_INSN_INVALID,
                              self._insn_invalid_hook)

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

        # Diagnostic: HAL_INSN_TRACE=<lo>-<hi> logs PC, SP, LR and the CPSR
        # IT-state for every instruction executed inside that address window.
        # HAL_LAST_PC only records basic-block STARTS, which is not enough when
        # the question is "did this one instruction execute?" — a stack-pointer
        # adjustment skipped inside a block is invisible at block granularity.
        _tr = _os.environ.get("HAL_INSN_TRACE")
        if _tr and arch_str == "arm":
            _lo, _, _hi = _tr.partition("-")
            _lo, _hi = int(_lo, 0), int(_hi, 0)
            _tr_state = {"n": 0}
            _tr_max = int(_os.environ.get("HAL_INSN_TRACE_MAX", "400"))

            def _insn_trace(uc, addr, size, ud):  # noqa: ANN001
                if _tr_state["n"] >= _tr_max:
                    return
                _tr_state["n"] += 1
                try:
                    sp = uc.reg_read(unicorn.arm_const.UC_ARM_REG_SP)
                    lr = uc.reg_read(unicorn.arm_const.UC_ARM_REG_LR)
                    cpsr = uc.reg_read(unicorn.arm_const.UC_ARM_REG_CPSR)
                except Exception:  # noqa: BLE001
                    return
                it = ((cpsr >> 25) & 3) | (((cpsr >> 10) & 0x3F) << 2)
                try:
                    raw = bytes(uc.mem_read(addr, 8)).hex()
                except Exception:  # noqa: BLE001
                    raw = "??"
                log.error("INSN: pc=0x%08x sz=%d sp=0x%08x lr=0x%08x it=%02x "
                          "bytes=%s", addr, size, sp, lr, it, raw)
            self._uc.hook_add(unicorn.UC_HOOK_CODE, _insn_trace,
                              begin=_lo, end=_hi)

        # Diagnostic: HAL_PC_SAMPLE=1 records a PC execution histogram so a
        # non-MMIO hang ("stuck where?") can be located. Dumped by
        # dump_pc_sample(). Off by default (no overhead).
        import os as _os
        if _os.environ.get("HAL_PC_SAMPLE"):
            import collections as _c
            self._pc_hist = _c.Counter()
            self._pc_n = 0
            _every = int(_os.environ.get("HAL_PC_SAMPLE_EVERY", "3000000"))
            _reset = _os.environ.get("HAL_PC_SAMPLE_RESET") == "1"

            def _pc_sample(uc, addr, size, ud):
                self._pc_hist[addr & ~1] += 1
                self._pc_n += 1
                if _every and self._pc_n % _every == 0:
                    self.dump_pc_sample()
                    # HAL_PC_SAMPLE_RESET=1 makes each dump a WINDOW rather
                    # than a running total. A cumulative histogram cannot show
                    # where the firmware is *now*: an early hot loop keeps the
                    # top-10 forever, so a later hang is invisible until it
                    # out-counts it. Off by default -- the running total is
                    # what you want for "what dominates the whole run".
                    if _reset:
                        self._pc_hist.clear()
            self._uc.hook_add(unicorn.UC_HOOK_CODE, _pc_sample)

        # HAL_DET_TICK deterministic system-clock tick is parsed in
        # _init_in_process_irq() (InProcessIrqMixin); cont() consumes
        # self._det_irq / _det_period / _det_chunks below.

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
        # Cortex-M supervisor call (`svc #n`). RTOS kernels use SVC for syscalls
        # and, in some ports, to start/switch threads (RIOT's cpu_switch_context_exit
        # issues `svc #1`; FreeRTOS's vPortStartFirstTask uses `svc`). (Note: Zephyr
        # on this target does NOT use svc to launch its main thread — it context-
        # switches via PENDSVSET from thread mode; see _maybe_deliver_thread_pendsv.
        # This trap is for the RTOSes that do use svc.) The generic ARM core Unicorn
        # boots does not vector M-profile SVC to the NVIC table, so the trap lands
        # here. Synthesise the
        # architectural entry to vector[11] (SVCall) via the shared in-process
        # delivery path so the firmware's OWN handler runs and returns via
        # EXC_RETURN (_maybe_handle_exc_return). skip_svc firmware (P2IM
        # instrumentation) opts out and takes the advance-past-SVC path below.
        if (self.arch_name == "cortex-m3" and not self.skip_svc
                and pc not in (-1, 0)
                and self._maybe_handle_cortexm_svc(uc, pc)):
            return
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
        # Thumb-2 IT-block repair (ARM only). See _repair_itstate.
        repair_it = self._is_thumb and self.arch_name in (
            "cortex-m3", "arm", "armv7a")

        def _hook(uc, access, addr, size, value, user_data):
            offset = addr - region.base_addr
            if access == unicorn.UC_MEM_READ and region.read_hook:
                result = region.read_hook(offset, size)
                if result is not None:
                    data = result.to_bytes(size, "little")
                    uc.mem_write(addr, data)
            elif access == unicorn.UC_MEM_WRITE and region.write_hook:
                region.write_hook(offset, size, value)
            if repair_it:
                self._repair_itstate(uc)
        return _hook

    @staticmethod
    def _repair_itstate(uc) -> None:
        """Clear a stale Thumb-2 ITSTATE left behind by a firing memory hook.

        Unicorn 2.1.4 (and every 2.x before it) leaks the IT state when one of
        our MMIO hooks fires on a load or store that sits INSIDE an `it` block:
        dispatching the hook restores the CPU state mid-block, which writes the
        block's ENTRY ITSTATE into the environment, and nothing advances or
        clears it afterwards. The value is a translation-block flag, so the NEXT
        block QEMU translates -- typically in the caller, after the peripheral
        driver returns -- is generated as though its first four instructions
        were that `it` block, and the ones whose condition now fails are
        silently skipped.

        This is not a rare corner. Compilers emit `it` blocks throughout
        Thumb-2, peripheral drivers read status registers inside them, and
        HALucinator hooks every modelled peripheral read. Worked example
        (ArduPilot/ChibiOS on an STM32F405): `palReadLineMode` reads GPIO
        registers inside an `iteet pl`; on return, the caller's
        `add sp, #36` was skipped, so `pop {r4-r7, pc}` took the wrong stack
        word and branched into a heap object. Deterministic, and it looks
        exactly like firmware memory corruption -- there is no fault at the
        peripheral, and the instruction that "did not happen" is four
        instructions away in a different function.

        Clearing the IT bits here is correct rather than merely convenient: the
        currently-executing block already has its conditions compiled in, so the
        real `it` block still runs exactly as it should; the write only stops
        the stale value from reaching the next block's translation flags. With
        this repair the executed instruction trace is identical to the same run
        with no MMIO hook installed at all (tests/test_unicorn_itstate.py).
        """
        try:
            cpsr = uc.reg_read(unicorn.arm_const.UC_ARM_REG_CPSR)
        except Exception:  # noqa: BLE001 — non-ARM or no CPSR exposed
            return
        # ITSTATE lives in CPSR[26:25] (IT[1:0]) and CPSR[15:10] (IT[7:2]).
        if not (cpsr & ((3 << 25) | (0x3F << 10))):
            return                      # not inside an `it` block: nothing to do
        try:
            uc.reg_write(unicorn.arm_const.UC_ARM_REG_CPSR,
                         cpsr & ~((3 << 25) | (0x3F << 10)))
        except Exception:  # noqa: BLE001
            pass

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
    # s0-s31 (aliased low halves); FPSCR is the status/control word; FPEXC
    # carries the VFP EN bit (bit 30). FPEXC is ESSENTIAL: unicorn's
    # context_save() does not preserve it, so without capturing/restoring it a
    # restored A-profile machine comes back with VFP disabled and the first
    # VFP instruction (e.g. `vpush {d8,d9}`) traps as UC_ERR_INSN_INVALID.
    _VFP_DREGS = tuple(f"UC_ARM_REG_D{i}" for i in range(32))
    _VFP_CTRL = ("FPEXC", "FPSCR")

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
            # Route via hal_log: the shipped logging.cfg leaves this module's
            # logger inheriting root=ERROR, so a plain log.warning here is
            # discarded by default -- and a silently lossy snapshot is
            # exactly what the caller needs told about.
            from halucinator import hal_log
            hal_log.getHalLogger().warning(
                "save_state(portable=True) on %s captures the general "
                "register file only — hidden system state is not yet "
                "enumerated for this arch", self.arch_name)
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
        # After a restore we are logically stopped at the snapshot PC, as if we
        # had just hit a breakpoint there. Reset the transient breakpoint
        # bookkeeping so a following continue_past_breakpoint() arms its
        # one-shot skip for THIS pc, not a stale address left over from the
        # pre-restore run. Otherwise, when the snapshot sits on a breakpoint
        # (e.g. a loop snapshotting at a marker breakpoint), the marker
        # re-triggers immediately and the resumed run is silently skipped —
        # a nasty source of flaky, order-dependent execution.
        self._skip_bp_once = None
        try:
            self._bp_hit_addr = self.read_register("pc") & 0xFFFFFFFE
        except Exception:  # noqa: BLE001
            self._bp_hit_addr = None
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

    def _install_bp_hook(self, addr: int) -> None:
        """Fast-bp mode: install a range-bounded UC_HOOK_CODE for one breakpoint
        address so Unicorn only enters _code_hook at that PC (blocks elsewhere
        run at full JIT speed). No-op unless the engine is up."""
        a = addr & 0xFFFFFFFE
        if self._uc is None or a in self._per_bp_hooks:
            return
        self._per_bp_hooks[a] = self._uc.hook_add(
            unicorn.UC_HOOK_CODE, self._code_hook, begin=a, end=a)

    def set_breakpoint(self, addr: int, hardware: bool = False,
                       temporary: bool = False) -> int:
        bp_id = self._next_bp_id
        self._next_bp_id += 1
        # Store with Thumb bit cleared for comparison in _code_hook
        self._breakpoints[addr & 0xFFFFFFFE] = bp_id
        if self._fast_bp_active:
            self._install_bp_hook(addr)
        return bp_id

    def remove_breakpoint(self, bp_id: int) -> None:
        to_remove = [a for a, bid in self._breakpoints.items() if bid == bp_id]
        for addr in to_remove:
            del self._breakpoints[addr]
            h = self._per_bp_hooks.pop(addr, None)
            if h is not None:
                try:
                    self._uc.hook_del(h)
                except Exception:  # noqa: BLE001
                    pass

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
        # Fast-BP eligibility is decided in init(); auto_recover_loops is a
        # public attribute a caller could flip afterwards, which would leave
        # the loop breaker silently dead (there is no global code hook to run
        # it in). Say so once rather than fail quietly.
        if (self._fast_bp_active and getattr(self, "auto_recover_loops", False)
                and not self._fast_bp_warned):
            self._fast_bp_warned = True
            hlog.warning("UnicornBackend: auto_recover_loops was enabled after "
                         "init() while HAL_FAST_BP is active -- the loop "
                         "breaker will NOT run. Unset HAL_FAST_BP to use it.")
        self._stopped = False
        self._bp_hit_addr = None
        # Fresh run: no exception-return continuation is owed from a prior cont().
        self._exc_return_pending = False
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
            # Let anything that needs a periodic tick have one, BEFORE the
            # queue is drained so a model can raise an interrupt here and see
            # it delivered on this same pass. See add_chunk_hook.
            self._run_chunk_hooks()
            # Drain any IRQs queued from another thread before
            # resuming — the synthetic exception frame setup mutates
            # PC/SP, only safe when emu_start is not running.
            self._drain_pending_irqs()
            # Deliver a PendSV requested from thread mode (the ICSR write hook
            # emu_stop'd us and parked PC on the store). Done here, at the top of
            # the loop, so it survives an intervening breakpoint stop: the parked
            # request stays set across a cont() return/re-entry (a bp handler
            # runs between) and is delivered when execution next resumes.
            if getattr(self, "_pendsv_store_parked", False):
                self._maybe_deliver_thread_pendsv()
            pc = self.read_register("pc")
            # Unicorn Thumb mode needs the LSB set on the start
            # address.
            start = (pc | 1) if self._is_thumb else pc
            try:
                self._uc.emu_start(start, until, timeout=0, count=irq_chunk)
            except unicorn.UcError as _uc_err:
                if self._stopped:
                    return  # stopped by breakpoint hook — normal
                # An exception return resolved the EXC_RETURN fetch and emu_stop'd;
                # some unicorn builds still surface the aborted run as a UcError.
                # Treat it exactly like the clean exc_return path: resume from the
                # restored PC rather than falling into bad-call/fault recovery.
                if self._exc_return_pending:
                    self._exc_return_pending = False
                    continue
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
                # inject_irq queued an IRQ on another thread, a thread-mode
                # PendSV request broke us out, or something asked us to stop.
                # Deliver / drain the former; otherwise honour the stop.
                if not self._stopped and getattr(self, "_pendsv_store_parked",
                                                  False):
                    continue
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
            # Cortex-M PendSV requested from thread mode (the ICSR write hook
            # emu_stop'd us, parking PC on the store). Loop back to the top,
            # which delivers it — unless a breakpoint also stopped us, in which
            # case honour the stop and deliver on the next cont() entry.
            if not self._stopped and getattr(self, "_pendsv_store_parked", False):
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
            # A Cortex-M exception return (_maybe_handle_exc_return) redirected PC
            # and emu_stop'd purely to restart at the restored PC — that internal
            # stop is neither a breakpoint nor an external stop(). Resume the run
            # from the restored PC instead of returning to the dispatch loop.
            # Returning here would (a) let the dispatch loop re-dispatch a
            # breakpoint the frame returned onto, which never re-enters emu_start
            # and so never fires the one-shot _skip_bp_once — an observe-only tick
            # handler (HAL_GetTick inject SysTick + continue_past_breakpoint) then
            # re-injects forever (livelock); and (b) exit outright when no IRQ
            # controller is configured (in_process_irq_active() is False), which
            # is exactly a native SVCall/PendSV-launched RTOS task landing at a
            # non-breakpoint PC (e.g. flipper's furi_thread_body). A real
            # breakpoint on the restored PC still stops us cleanly: the code hook
            # fires on re-entry, sets _stopped, and we fall through to return.
            if self._exc_return_pending and not self._stopped:
                self._exc_return_pending = False
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
    # EXC_RETURN constants + frame decode now live in InProcessIrqMixin.

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

    @property
    def arch(self) -> str:
        """Alias for arch_name, so the shared InProcessIrqMixin can read a
        uniform ``arch`` attribute across backends."""
        return self.arch_name

    def _request_break(self) -> None:
        """Thread-safe stop of the running emulator (InProcessIrqMixin
        primitive). Unicorn raises if not currently running — ignore."""
        if self._uc is None:
            return
        try:
            self._uc.emu_stop()
        except Exception:  # noqa: BLE001
            pass

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

    def _apply_cortex_m_fallback(self, irq_num: int) -> None:
        """Cortex-M (and any un-migrated arch) fallback: push the 8-word
        exception frame on the main stack and vector to vector[16+N].
        Called by InProcessIrqMixin._apply_pending_irq. Must run on the
        dispatch thread — Unicorn isn't safe against PC/SP writes mid-run."""
        if self._uc is None:
            return

        # Vector table offset. `set_vtor()` plumbs in the *configured* base,
        # which is where the table is at reset -- but firmware relocates it.
        # A bootloader hands off to an application with its own table, an RTOS
        # copies the table to RAM to patch it, and a Nordic SoftDevice inserts
        # itself between the two: MBR at 0x0, SoftDevice at 0x1000,
        # application at 0x1c000, with SCB->VTOR moved at each handoff.
        #
        # Delivering to the reset-time table in that situation is not a
        # near-miss, it is a jump into an unrelated binary's handler. On the
        # nRF52832 BLE device it sent the application's SWI0 (app_timer) into
        # the MBR's slot 20 -- 0x00000687 instead of 0x0001c819 -- and the
        # machine wedged with no fault, no console output, and almost no
        # instructions retired.
        #
        # So prefer what the firmware has actually programmed, when it can be
        # read back. A *modelled* PPB intercepts the write and never puts it in
        # memory, which is why models are expected to call set_vtor() (see
        # _vtor_from_guest for the ordering between the two).
        vtor = self._effective_vtor()
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

        # Take the exception the way ARMv7-M hardware does — PSP-aware, so a
        # preemptive RTOS (mbed RTX etc.) can context-switch. Push the 8-word
        # frame onto the ACTIVE stack (PSP when in a thread with CONTROL.SPSEL,
        # else MSP), set LR to the matching EXC_RETURN (…F1 handler/MSP,
        # …F9 thread/MSP, …FD thread/PSP), then enter handler mode (IPSR = the
        # exception number) so the handler runs on MSP. If the active SP isn't
        # writable (early boot / a mid-switch window), real hardware wouldn't
        # take the interrupt either — drop this delivery rather than crash.
        import struct
        from unicorn import arm_const as _A
        exc_num = (16 + irq_num) & 0x1FF
        ipsr = self._uc.reg_read(_A.UC_ARM_REG_IPSR) & 0x1FF
        control = self._uc.reg_read(_A.UC_ARM_REG_CONTROL)
        in_thread = ipsr == 0
        # Which stack is the interrupted thread on? Normally CONTROL.SPSEL
        # says. Once we are banking by hand (below) CONTROL is frozen and
        # lying, so the shadow is the only truthful answer.
        if self._m_manual_bank:
            use_psp = in_thread and self._m_spsel
        else:
            use_psp = in_thread and bool(control & 2)      # SPSEL
        xpsr = self._uc.reg_read(_A.UC_ARM_REG_XPSR)
        frame = struct.pack("<8I",
                            self.read_register("r0"), self.read_register("r1"),
                            self.read_register("r2"), self.read_register("r3"),
                            self.read_register("r12"), self.read_register("lr"),
                            self.read_register("pc"), xpsr)

        # ARMv7E-M with an FPU (Cortex-M4F/M7): when the interrupted context
        # has live floating-point state — CONTROL.FPCA set — the hardware
        # stacks an EXTENDED frame (the 8 words above, then S0-S15, FPSCR and
        # one reserved word: 104 bytes total) and clears bit 4 of EXC_RETURN
        # to say so. Pushing the basic frame regardless is self-consistent
        # only until the firmware does its own frame arithmetic: an RTOS that
        # inspects EXC_RETURN, or code the compiler gave FP locals, then
        # unwinds the wrong number of words. Observed on ArduPilot/ChibiOS
        # (Cortex-M4F): a constructor returned into a heap pointer,
        # deterministically, and never with interrupts disabled.
        fpca = bool(control & 4)
        if fpca and self._fp_regs_available():
            fp_words = [self._uc.reg_read(getattr(_A, "UC_ARM_REG_S%d" % i))
                        for i in range(16)]
            fpscr = self._uc.reg_read(_A.UC_ARM_REG_FPSCR)
            frame = frame + struct.pack("<18I", *fp_words, fpscr, 0)
        # Stack the frame on r13, NOT on the MSP/PSP alias `use_psp` selects.
        # Exception entry always pushes to whichever stack is currently
        # active, and that is r13 by definition — so this is exact, not an
        # approximation.
        #
        # It is also the only read that WORKS. MSP and PSP are reached through
        # QEMU's MRS/MSR special-register helpers, which return 0 (and discard
        # writes) when the core is UNPRIVILEGED — the architectural behaviour
        # of `MRS Rn, MSP` from an unprivileged thread. Firmware that drops
        # privilege (`msr control, #3`, as every MPU-hardened image does) made
        # every delivery read the active stack as 0, compute sp = -32, fail
        # the mem_write and silently drop the exception. Nothing faults: the
        # firmware simply never takes an interrupt again. KeepKey routes all
        # flash writes through `svc`, so the visible symptom was a wallet that
        # ran perfectly and could not persist a single byte.
        #
        # `use_psp` still selects the EXC_RETURN value below, which is what
        # tells the firmware's handler (and _maybe_handle_exc_return) which
        # bank the frame is on.
        sp = self._uc.reg_read(_A.UC_ARM_REG_SP) - len(frame)  # 8-aligned
        try:
            self._uc.mem_write(sp, frame)
        except Exception:  # noqa: BLE001 — unmapped/invalid SP: skip this tick
            log.debug("inject_irq(%d): SP 0x%x not writable, dropping delivery",
                      irq_num, sp)
            return
        self._uc.reg_write(_A.UC_ARM_REG_SP, sp)
        exc_ret = (0xFFFFFFF1 if not in_thread
                   else 0xFFFFFFFD if use_psp else 0xFFFFFFF9)
        if len(frame) > 32:
            exc_ret &= ~0x10          # bit4 clear: extended (FP) frame stacked
            # Entering the handler clears FPCA, as the hardware does.
            try:
                self._uc.reg_write(_A.UC_ARM_REG_CONTROL, control & ~4)
            except Exception:  # noqa: BLE001
                pass
        self.write_register("lr", exc_ret)
        self._uc.reg_write(_A.UC_ARM_REG_IPSR, exc_num)    # -> handler mode (MSP)
        if self._m_manual_bank and in_thread:
            # SPSEL is frozen, so the IPSR write above did not necessarily move
            # the banks the way hardware would. We are in handler mode now,
            # which is the ONLY state where MSP/PSP are writable, so put them
            # where the firmware's own handler will look:
            #   - the frame on the stack EXC_RETURN advertises, because
            #     handlers read it back with `MRS Rn, PSP` / `MRS Rn, MSP`;
            #   - the handler itself on the main stack.
            if use_psp:
                self._uc.reg_write(_A.UC_ARM_REG_PSP, sp)
                if self._m_saved_msp is not None:
                    self._uc.reg_write(_A.UC_ARM_REG_MSP, self._m_saved_msp)
            else:
                self._uc.reg_write(_A.UC_ARM_REG_MSP, sp)
                self._m_saved_msp = sp
        self._icsr_enter(exc_num, nested=not in_thread)
        self.write_register("pc", isr_addr & ~1)  # Thumb bit goes in CPSR.T
        log.info("inject_irq(%d): exc %d @ 0x%x (exc_return %#x)",
                 irq_num, exc_num, isr_addr, exc_ret)

    # SCB->ICSR. Bits we own here: VECTACTIVE[8:0] — the exception number the
    # CPU is currently executing — and RETTOBASE[11] — "returning from this
    # exception returns to base level", i.e. no other exception is active.
    # Everything else in the register (PENDSVSET etc.) belongs to the firmware
    # and is preserved.
    _ICSR = 0xE000ED04
    _ICSR_VECTACTIVE = 0x1FF
    _ICSR_RETTOBASE = 1 << 11

    def _icsr_update(self, vectactive: int, rettobase: bool) -> None:
        """Read-modify-write SCB->ICSR's VECTACTIVE/RETTOBASE.

        This backend delivers Cortex-M exceptions itself and leaves the private
        peripheral bus as plain RW memory, so nothing was maintaining ICSR: it
        read 0 forever. That is not a cosmetic gap. ChibiOS' ARMv7-M ISR
        epilogue is::

            ldr  r3, [SCB_ICSR]
            ands r3, #0x800          @ RETTOBASE
            beq  no_reschedule

        so with RETTOBASE stuck at 0 the kernel takes the interrupt, runs the
        tick, readies the woken thread — and then skips the deferred context
        switch every single time. Observed on ArduPilot/ChibiOS: the vehicle
        clock advanced, virtual timers expired and the alarm was disarmed, but
        execution returned to the idle thread on every tick and no ArduPilot
        thread ever ran. Firmware that asks "am I in an interrupt?" via
        VECTACTIVE (rather than IPSR) is wrong in the same silent way.
        """
        if self._uc is None:
            return
        try:
            cur = int.from_bytes(self._uc.mem_read(self._ICSR, 4), "little")
            new = cur & ~(self._ICSR_VECTACTIVE | self._ICSR_RETTOBASE)
            new |= vectactive & self._ICSR_VECTACTIVE
            if rettobase:
                new |= self._ICSR_RETTOBASE
            self._uc.mem_write(self._ICSR, new.to_bytes(4, "little"))
        except Exception:  # noqa: BLE001 — PPB unmapped: nothing to maintain
            pass

    def _icsr_enter(self, exc_num: int, nested: bool) -> None:
        """Entering exception `exc_num`. RETTOBASE is set unless this one
        preempted another active exception."""
        self._exc_depth = getattr(self, "_exc_depth", 0) + 1
        self._icsr_update(exc_num, rettobase=not nested)

    def _icsr_exit(self, new_ipsr: int) -> None:
        """Leaving an exception for `new_ipsr` (0 = thread mode)."""
        self._exc_depth = max(0, getattr(self, "_exc_depth", 0) - 1)
        self._icsr_update(new_ipsr, rettobase=self._exc_depth <= 1)

    def _fp_regs_available(self) -> bool:
        """True when this unicorn build exposes S0-S15 and FPSCR."""
        cached = getattr(self, "_fp_regs_ok", None)
        if cached is not None:
            return cached
        from unicorn import arm_const as _A
        ok = hasattr(_A, "UC_ARM_REG_FPSCR") and hasattr(_A, "UC_ARM_REG_S15")
        if ok:
            try:
                self._uc.reg_read(_A.UC_ARM_REG_FPSCR)
            except Exception:  # noqa: BLE001
                ok = False
        if not ok:
            hlog.warning("UnicornBackend: this unicorn build has no FP "
                         "registers; exceptions taken with CONTROL.FPCA set "
                         "will stack a basic frame, which an FPU firmware may "
                         "unwind incorrectly")
        self._fp_regs_ok = ok
        return ok

    # SCB->VTOR on ARMv7-M. The table must be aligned to at least 128 bytes
    # (and to a power of two >= 4 * the number of exceptions), so a value that
    # is not is not a vector table and must not be believed.
    _VTOR_ADDR = 0xE000ED08
    _VTOR_ALIGN = 0x80

    def set_vtor(self, vtor: int) -> None:
        """Set the vector-table base so inject_irq can find ISRs.

        Called by main.py with the configured reset-time base, and *also*
        intended to be called by a peripheral model that owns the PPB when it
        sees the firmware write SCB->VTOR: a modelled region intercepts the
        write, so the value never reaches guest memory for
        :meth:`_effective_vtor` to find.
        """
        if vtor != getattr(self, "_vtor", None):
            log.info("cortex-m: vector table base -> 0x%08x", vtor)
        self._vtor = vtor

    def _effective_vtor(self) -> int:
        """The vector base the firmware is actually using, if discoverable.

        Reads SCB->VTOR out of guest memory, which works whenever the PPB is
        plain backend-mapped memory (the default when no model claims it). If
        the read fails, returns zero, or is not a legally aligned table base,
        fall back to whatever ``set_vtor`` was last given -- which is the
        configured base, or the value a PPB model plumbed in.
        """
        configured = getattr(self, "_vtor", 0)
        if self._uc is None:
            return configured
        try:
            live = int.from_bytes(
                self._uc.mem_read(self._VTOR_ADDR, 4), "little")
        except Exception:  # noqa: BLE001 — unicorn raises if unmapped
            return configured
        if not live or live % self._VTOR_ALIGN:
            return configured
        if live != getattr(self, "_vtor_seen", None):
            self._vtor_seen = live
            if live != configured:
                log.info("cortex-m: firmware relocated the vector table to "
                         "0x%08x (configured base was 0x%08x); interrupts will "
                         "be delivered through the new table", live, configured)
        return live

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
        """A-profile ARM IRQ delivery (Unicorn). Preferred path: the
        ExceptionDeliverer + DeliveryPlan set via main._wire_irq (subsumes
        the ArmVicController synth path and the legacy GIC path, proven
        equivalent in test_arm_deliverer_equivalence.py). Falls back to the
        legacy ArmVicController.deliver, then the built-in ARMv7-A/GIC entry
        (VBAR+0x18 + GICC_IAR shadow). Only the legacy GIC path adds the
        masked-IRQ re-queue (CPSR.I=1 -> re-queue + stop so the firmware can
        unmask); the deliverer / ArmVicController paths drop the tick."""
        from halucinator.backends.irq.delivery import (
            ArmExceptionDeliverer, DeliveryModel, DeliveryPlan)
        deliverer = getattr(self, "_exception_deliverer", None)
        plan = getattr(self, "_delivery_plan", None)
        if (deliverer is not None and plan is not None
                and plan.model in (DeliveryModel.FRAME,
                                   DeliveryModel.TRAMPOLINE)):
            deliverer.deliver(self, irq_num, plan)
            return
        ctrl = getattr(self, "_irq_controller", None)
        if ctrl is not None and hasattr(ctrl, "deliver"):
            # deliver() returns False when CPSR.I masks IRQs — drop that tick
            # (the next periodic tick lands once firmware re-enables IRQs); do
            # NOT re-queue, which would busy-spin re-applying an un-enterable
            # tick.
            ctrl.deliver(self, irq_num)
            return
        gicc_base = getattr(ctrl, "gicc_base", None) if ctrl else None
        vbar = getattr(self, "_vtor", 0)
        lplan = DeliveryPlan(model=DeliveryModel.FRAME, vector_base=vbar,
                             gicc_base=gicc_base)
        delivered = ArmExceptionDeliverer().deliver(self, irq_num, lplan)
        if not delivered:
            # IRQs masked — re-queue and let the firmware unmask itself.
            self._pending_irqs.insert(0, irq_num)
            self._request_break()
            return
        log.info("inject_irq(%d): ARMv7-A entry @ 0x%x", irq_num, vbar + 0x18)

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

    # PendSV is exception 14, delivered through _apply_cortex_m_fallback as
    # irq_num -2 (16 + -2 == 14), the same shared path as external IRQs.
    _PENDSV_IRQ = -2

    def _cortexm_step_one(self) -> None:
        """Execute exactly one instruction from the current PC.

        Used to retire the `str ICSR,PENDSVSET` store the write hook parked PC
        on (emu_stop aborts the faulting instruction, leaving PC on it). The
        hook's already-pending guard keeps this single step from being
        re-broken by the same store's write hook."""
        if self._uc is None:
            return
        pc = self.read_register("pc")
        start = (pc | 1) if self._is_thumb else pc
        until = (1 << (self._word_size * 8)) - 1
        try:
            self._uc.emu_start(start, until, timeout=0, count=1)
        except unicorn.UcError:
            pass

    def _maybe_deliver_thread_pendsv(self) -> bool:
        """Deliver a PendSV that was requested from THREAD mode (the ICSR write
        hook emu_stop'd us with ``_pendsv_pending`` set). Runs from cont()
        between emu_start calls, where mutating PC/SP is safe.

        Retires the parked PENDSVSET store first, then — only if the CPU is
        back in thread mode (a PendSV, the lowest-priority exception, must not
        nest inside an active handler) — synthesises the PendSV entry through
        the shared ``_apply_cortex_m_fallback`` path (irq -2 -> exception 14),
        exactly as a tail-chained PendSV is. If still in a handler, leaves the
        request pending for the exc_return tail-chain to deliver.

        Returns True when it delivered (or retired the parked store), so cont()
        re-enters emu_start rather than treating the emu_stop as a final stop.
        """
        if getattr(self, "_pendsv_store_parked", False):
            self._pendsv_store_parked = False
            # Retire the parked store under the stepping guard so its re-write
            # of PENDSVSET does not re-park (which would abort the step and pin
            # PC on the store, re-pending PendSV forever).
            self._pendsv_stepping = True
            try:
                self._cortexm_step_one()
            finally:
                self._pendsv_stepping = False
        try:
            ipsr = self._uc.reg_read(unicorn.arm_const.UC_ARM_REG_IPSR) & 0x1FF
        except Exception:  # noqa: BLE001
            ipsr = 0
        if ipsr != 0:            # in a handler: leave pending for exc_return
            return False
        self._pendsv_pending = False
        self._apply_cortex_m_fallback(self._PENDSV_IRQ)
        return True

    # SVCall (exception 11) is delivered through the same in-process path as
    # every other Cortex-M exception: _apply_pending_irq (InProcessIrqMixin)
    # -> _apply_cortex_m_fallback, which computes exc_num = 16 + irq_num. So
    # SVCall is queued as irq_num -5 (16 + -5 == 11), mirroring PendSV's -2.
    _SVCALL_IRQ = -5

    def _maybe_handle_cortexm_svc(self, uc, pc: int) -> bool:
        """Synthesise a Cortex-M SVCall (``svc`` instruction) exception entry.

        RTOS kernels start their first thread and yield via ``svc``: Zephyr's
        ``z_arm_svc`` (``arch_switch_to_main_thread``), FreeRTOS's
        ``vPortSVCHandler``, RIOT's ``isr_svc``. The generic ARM core Unicorn
        boots does not architecturally vector M-profile SVC to the NVIC vector
        table, so the trap surfaces here in the INTR hook with PC at the
        instruction *after* the ``svc`` — the 16-bit Thumb opcode is ``0xDFxx``,
        i.e. two bytes back at ``pc-2``.

        We verify the opcode and QUEUE a synthetic exception entry to SVCall
        (vector slot 11) through the shared in-process delivery path — the SAME
        ``_apply_cortex_m_fallback`` frame-push/vectoring used for external IRQs
        and PendSV (via ``InProcessIrqMixin._apply_pending_irq``). Queueing
        rather than mutating PC/SP inline matters: the frame push is only safe
        between ``emu_start`` calls, so we append the pending IRQ and stop, and
        ``cont()`` drains it at the top of its loop (exactly as PendSV is).

        The return address stacked is the current PC (the instruction after the
        ``svc``); the firmware's own handler runs and returns via EXC_RETURN
        (unwound by ``_maybe_handle_exc_return``). Faithful: no firmware skip,
        the kernel's context switch executes for real.

        Returns True (and ``emu_stop``s) when an SVC entry was queued, else
        False.
        """
        if self.arch_name != "cortex-m3":
            return False
        # Confirm a 16-bit Thumb SVC (0xDF nn) sits just before PC. Unicorn
        # reports PC at the next instruction, so the opcode is at pc-2.
        try:
            op = bytes(uc.mem_read(pc - 2, 2))
        except Exception:  # noqa: BLE001 — Unicorn raises UcError on bad read
            return False
        if len(op) != 2 or op[1] != 0xDF:
            return False
        # Diagnostic: which supervisor call, and how often. An RTOS or a vendor
        # stack issues a handful of distinct SVC numbers; a firmware stuck in a
        # supervisor-call storm issues ONE, millions of times, and that number
        # names the API it is stuck in. HAL_SVC_TRACE=<n> reports the first n
        # and then every millionth.
        self._svc_count = getattr(self, "_svc_count", 0) + 1
        if self._svc_trace_n and (self._svc_count <= self._svc_trace_n
                                  or self._svc_count % 1000000 == 0):
            extra = ""
            if self._svc_trace_probe is not None:
                # A vendor stack dispatches supervisor calls through pointers it
                # keeps in RAM (a Nordic SoftDevice uses two: the active vector
                # table and the application's). When SVC dispatch misbehaves,
                # the question is always "what did it read", so allow one word
                # to be dumped alongside each call.
                try:
                    word = int.from_bytes(
                        uc.mem_read(self._svc_trace_probe, 4), "little")
                    extra = " [0x%08x]=0x%08x" % (self._svc_trace_probe, word)
                except Exception:  # noqa: BLE001 — unmapped probe address
                    extra = " [0x%08x]=<unmapped>" % self._svc_trace_probe
            log.info("cortex-m: svc #0x%02x from 0x%08x (call %d)%s",
                     op[0], pc - 2, self._svc_count, extra)
        # Queue SVCall (exc 11) via the shared mixin delivery path and break out
        # of emu_start; cont() applies it (pushes the frame, vectors to slot 11)
        # before re-entering at the handler. Same mechanism inject_irq/PendSV use.
        self._pending_irqs.append(self._SVCALL_IRQ)
        uc.emu_stop()
        return True

    def add_chunk_hook(self, callback) -> None:
        """Register a callable to run at every instruction-chunk boundary.

        A PERIODIC TICK THAT DOES NOT DEPEND ON THE FIRMWARE TOUCHING ANYTHING.
        Peripheral models normally advance their notion of time from MMIO
        activity, because that is the only thing they are called for. That
        works right up until the firmware idles -- and idling is exactly when
        the timers matter, because the interrupt that ends the idle is the one
        a timer is supposed to raise.

        Nordic's S132 shows the shape clearly. Its scheduler arms RTC0 for the
        next advertising event and then waits in

            wfe ; ldr r0,[r4] ; ldrb r0,[r0,#0x1c] ; bl … ; cmp r0,#0 ; beq

        which reads only RAM. No MMIO, so an MMIO-driven clock stops dead, the
        RTC never reaches the compare it was armed for, and the device
        advertises twice and then sleeps for ever. Nothing faults, and the
        firmware is behaving perfectly correctly.

        A chunk boundary is the natural place for this: it is reached every
        HAL_IRQ_CHUNK instructions regardless of what the guest is doing, it is
        where CPU state is already safe to mutate, and it is where queued
        interrupts are delivered. Hooks run *before* that drain, so a model can
        raise a line and have it taken on the same pass.

        Exceptions from a hook are logged and swallowed: a model must not be
        able to kill the run from its own timekeeping.
        """
        if not hasattr(self, "_chunk_hooks"):
            self._chunk_hooks = []
        if callback not in self._chunk_hooks:
            self._chunk_hooks.append(callback)

    def _run_chunk_hooks(self) -> None:
        for callback in getattr(self, "_chunk_hooks", ()):  # noqa: B007
            try:
                callback()
            except Exception:  # noqa: BLE001 — a model's tick must not abort
                log.exception("chunk hook %r failed", callback)

    def _drain_pending_irqs(self) -> None:
        """Apply every queued exception -- SYNCHRONOUS ONES FIRST.

        WHY THE ORDER IS NOT ARBITRARY. This queue mixes two different kinds of
        thing. An external interrupt is *asynchronous*: it may be taken between
        any two instructions, so stacking whatever PC the CPU happens to be at
        is always right. A ``svc`` is *synchronous and precise*: by the time it
        reaches this queue the instruction has already executed, and the frame
        must stack the address **immediately after it**, because that is the
        only state consistent with what the CPU actually did.

        Take an interrupt first and that invariant is broken. PC has already
        moved to the interrupt's handler, so the SVCall frame stacks the
        handler's entry address instead -- and every ARMv7-M SVC dispatcher in
        existence recovers the call number by reading ``stacked_PC - 2`` and
        taking the low byte of the ``svc`` opcode. It therefore reads a byte of
        whatever code the interrupt vectored to, and dispatches on it.

        Observed on the nRF52832 + S132 device, and it is worth recording
        because the failure has no fault and no console output:

            svc #0x48 from 0x0002032c        the guest's real call
            inject_irq(20): exc 36 @ 0x687   drained first -- PC moves to the
                                             MBR's SWI0 forwarder
            inject_irq(-5): exc 11 @ 0x909   SVCall stacks PC=0x687

        The SoftDevice's dispatcher then read ``[0x685]`` -- a byte of MBR
        forwarder code -- as the call number, found it below 0x10, and forwarded
        it to the *application's* SVCall handler, which in this image is the
        default ``b .``. The machine sat there executing one instruction 82
        million times.

        The interrupt is still delivered, immediately afterwards, stacking the
        SVCall handler's entry address. That is a legal and ordinary nesting: a
        higher-priority interrupt pre-empting a supervisor call is exactly what
        the priority scheme is for, and the SVCall handler runs when it returns.
        """
        queue = self._pending_irqs
        while queue:
            if self._SVCALL_IRQ in queue:
                queue.remove(self._SVCALL_IRQ)
                self._apply_pending_irq(self._SVCALL_IRQ)
                continue
            self._apply_pending_irq(queue.pop(0))

    _WFE_THUMB = 0xBF20                     # `wfe` -- the one M-profile hint
                                            # unicorn's decoder rejects

    def _insn_invalid_hook(self, uc, user_data) -> bool:
        """Execute a Cortex-M ``wfe`` as the no-op it is permitted to be.

        Unicorn's M-profile decoder accepts ``wfi`` and ``sev`` but NOT
        ``wfe``: 0xBF20 raises UC_ERR_INSN_INVALID on every M-profile CPU model
        it offers (M3, M4, M7, M33 all verified). That kills any firmware
        idling on the wait-for-event idiom -- CMSIS ``__WFE()``, most RTOS idle
        loops, and Nordic's CryptoCell driver, which spins
        ``wfe; dmb; ldr; tst; beq`` waiting for its completion interrupt.

        Skipping it is architecturally correct rather than a shortcut: WFE is a
        hint, and the architecture explicitly permits it to complete
        immediately (it returns as soon as the event register is set, and the
        register may already be set on entry). Nothing is lost in a rehost,
        where the event the firmware waits for is delivered by an injected
        interrupt anyway.

        WHEN THIS BITES, IT POINTS AT THE WRONG INSTRUCTION. Unicorn reports the
        fault with PC already advanced past the ``wfe``, so the disassembly at
        the reported address is an innocent bystander -- a ``dmb``, an ``ldr``
        -- and the obvious next step (work out why unicorn cannot decode a
        barrier) is a dead end. The opcode under test is therefore at ``pc-2``.

        Returning True tells unicorn the instruction was handled and execution
        continues; returning False lets a genuinely undefined instruction
        surface as the error it is.
        """
        if self.arch_name != "cortex-m3":
            return False
        from unicorn import arm_const as _A
        try:
            pc = uc.reg_read(_A.UC_ARM_REG_PC)
            if pc < 2:
                return False
            opcode = int.from_bytes(uc.mem_read(pc - 2, 2), "little")
        except Exception:  # noqa: BLE001 — unicorn raises if unmapped
            return False
        if opcode != self._WFE_THUMB:
            return False
        self._wfe_skipped += 1
        if self._wfe_skipped == 1:
            log.info("cortex-m: `wfe` at 0x%08x executed as a no-op (unicorn's "
                     "M-profile decoder rejects 0xBF20; the architecture "
                     "permits WFE to complete immediately)", pc - 2)
        # A firmware that idles on WFE reaches this a handful of times per
        # main-loop pass. One that reaches it MILLIONS of times is not idling,
        # it is waiting for an event that this rehost is never going to deliver
        # -- and because skipping WFE is silent and correct, that failure has no
        # other symptom: no fault, no console output, and a guest that appears
        # to be running normally while executing almost nothing. Say so.
        elif self._wfe_skipped % 1000000 == 0:
            log.warning("cortex-m: `wfe` skipped %d times (currently at "
                        "0x%08x). This firmware is spinning on an event that "
                        "is not arriving -- check that the interrupt it is "
                        "waiting for is actually being delivered.",
                        self._wfe_skipped, pc - 2)
        # PC has already advanced past the wfe; keep the Thumb bit.
        uc.reg_write(_A.UC_ARM_REG_PC, pc | 1)
        return True

    def _maybe_handle_exc_return(self, addr: int) -> bool:
        """Called from the invalid-fetch hook. If the fetch address is an
        EXC_RETURN magic value, pop the exception frame from the stack the
        EXC_RETURN selects (MSP or PSP), restore thread/handler mode + SPSEL,
        and resume. PSP-aware so an RTOS context switch (PendSV returning via
        …FD to the switched-in thread's PSP) lands on the new thread."""
        if self.arch_name != "cortex-m3":
            return False
        if (addr & self._EXC_RETURN_MASK) != self._EXC_RETURN_MAGIC:
            return False
        import struct
        from unicorn import arm_const as _A
        return_psp = bool(addr & 0x4)          # EXC_RETURN bit2: return stack
        return_thread = bool(addr & 0x8)       # bit3: return mode
        active = _A.UC_ARM_REG_PSP if return_psp else _A.UC_ARM_REG_MSP
        sp = self._uc.reg_read(active)
        # EXC_RETURN bit 4 clear means the hardware stacked the EXTENDED
        # (floating-point) frame: 8 words, then S0-S15, FPSCR and a reserved
        # word. Unwinding 8 words from an extended frame leaves SP 72 bytes
        # low and every later return goes to garbage.
        extended = not (addr & 0x10)
        try:
            frame = struct.unpack("<8I", bytes(self._uc.mem_read(sp, 32)))
        except Exception:                      # noqa: BLE001
            return False
        self.write_register("r0", frame[0])
        self.write_register("r1", frame[1])
        self.write_register("r2", frame[2])
        self.write_register("r3", frame[3])
        self.write_register("r12", frame[4])
        self.write_register("lr", frame[5])
        if extended and self._fp_regs_available():
            try:
                fp = struct.unpack("<18I", bytes(self._uc.mem_read(sp + 32, 72)))
                for i in range(16):
                    self._uc.reg_write(getattr(_A, "UC_ARM_REG_S%d" % i), fp[i])
                self._uc.reg_write(_A.UC_ARM_REG_FPSCR, fp[16])
            except Exception:  # noqa: BLE001
                pass
        # ARMv7-M B1.5.8 (ExceptionReturn): the exception return SETS
        # CONTROL.FPCA from the frame type it just unwound —
        # `CONTROL.FPCA = NOT EXC_RETURN[4]` — in BOTH directions. Only the
        # "set on an extended return" half used to be implemented, and the
        # missing half is a silent, compounding bug on any M4F/M7 RTOS:
        #
        #   * FPCA is set by executing ANY FP instruction, including inside a
        #     handler. FreeRTOS's ARM_CM4F PendSV runs `vstmdb`/`vldmia` on
        #     s16-s31, so FPCA is set every context switch.
        #   * With no clear-on-basic-return, FPCA then stays set in a thread
        #     that owns no FP state at all, and the NEXT exception entry stacks
        #     the 104-byte EXTENDED frame instead of the 32-byte basic one.
        #   * A small stack cannot absorb that. Measured on
        #     device-duet2-wifi-eth (RepRapFirmware 3.6.3, SAM4E8E): FreeRTOS's
        #     IDLE task has a 200-byte stack; 104 (hardware frame) + 100
        #     (PendSV's r4-r11/lr + s16-s31) = 204, so the RTOS's own overflow
        #     check fired and the firmware reset itself —
        #     "SoftwareReset reason=0x0100 (StackOverflow) task='IDLE'".
        #     Nothing faults in the emulator; the firmware simply panics, and
        #     the cause is 100+ exceptions upstream of the symptom.
        control = self._uc.reg_read(_A.UC_ARM_REG_CONTROL)
        control = (control | 4) if extended else (control & ~4)
        try:
            self._uc.reg_write(_A.UC_ARM_REG_CONTROL, control)
        except Exception:  # noqa: BLE001
            pass
        thread_sp = sp + (104 if extended else 32)
        # WHY THIS IS NOT JUST `reg_write(active, thread_sp)`.
        #
        # An exception return has to put CONTROL.SPSEL back to the stack the
        # EXC_RETURN selects. But QEMU implements the architectural rule that
        # `MSR CONTROL` is IGNORED while CONTROL.nPRIV is set -- and unicorn's
        # register API goes through the same helper. So the moment firmware
        # drops privilege, the backend can NEVER correct SPSEL again: the write
        # below succeeds silently and changes nothing, and every later exception
        # entry then reads SPSEL=0, advertises EXC_RETURN as "main stack", and
        # hands the firmware's handler a frame pointer into the wrong stack.
        #
        # That is not a hypothetical. On device-trezor-modelt (a privileged
        # kernel plus an unprivileged applet, switched by PendSV) the applet
        # returned via EXC_RETURN 0xFFFFFFED (thread, PSP) and CONTROL stayed
        # 0x5 -- SPSEL clear. The next `svc` was advertised as 0xFFFFFFE9, so
        # SVC_Handler's `TST LR,#4 / MRSEQ R0,MSP` read the frame off the
        # KERNEL stack, decoded a garbage syscall number, and wrote its result
        # back over the kernel's frames. Nothing faulted for another two
        # exceptions.
        #
        # MSP and PSP are writable only in HANDLER mode (thread+unprivileged
        # reads them as 0 and drops writes), so all of this has to happen here,
        # before the IPSR write below returns us to thread mode.
        control_now = self._uc.reg_read(_A.UC_ARM_REG_CONTROL)
        if (return_thread and (control_now & 1)
                and bool(control_now & 2) != return_psp):
            self._m_manual_bank = True
        if self._m_manual_bank and return_thread:
            self._m_spsel = return_psp
            # The handler's own stack, saved before we overwrite it, so the
            # next entry from a PSP thread can hand it back.
            self._m_saved_msp = self._uc.reg_read(_A.UC_ARM_REG_MSP)
            # SPSEL is frozen, so we cannot predict which bank the IPSR write
            # leaves active -- write the thread's stack into both.
            self._uc.reg_write(_A.UC_ARM_REG_MSP, thread_sp)
            self._uc.reg_write(_A.UC_ARM_REG_PSP, thread_sp)
        else:
            # Returning to HANDLER mode (a nested exception unwinding to the
            # handler it pre-empted) must NOT touch the banks: the outer
            # handler keeps its own MSP, and the PSP still belongs to the
            # interrupted thread. Writing both here destroys the thread's
            # stack pointer, and the damage only surfaces at the NEXT return
            # to thread mode -- as a garbage EXC_RETURN out of the firmware's
            # own SVC handler.
            self._uc.reg_write(active, thread_sp)
        self.write_register("cpsr", frame[7])  # restores the APSR flags
        # ...but NOT the exception number. Unicorn's CPSR write does not touch
        # `env->v7m.exception` on an M-profile core, so IPSR has to be written
        # explicitly -- for BOTH kinds of return, not just the thread one.
        #
        # Getting only the thread case right is a silent, long-lived error. A
        # nested exception unwinding into the handler it preempted
        # (EXC_RETURN 0xFFFFFFF1/0xFFFFFFE1) left IPSR reading the INNER
        # exception for the whole remaining life of the OUTER handler. Firmware
        # that asks the core "which exception am I in?" -- rusEFI/FOME's
        # assertInterruptPriority() does exactly this, then indexes
        # NVIC->IP[n] -- reads a priority byte nobody ever wrote and latches a
        # firmware error. And any rehost whose pump refuses to inject while
        # IPSR != 0 (the architecturally correct rule) goes permanently deaf
        # after the first nested return: the guest really is back in the outer
        # handler, but the pump can never tell that it left the inner one.
        #
        # Hardware restores the whole xPSR from the stacked frame, exception
        # number included.
        if return_thread:
            self._uc.reg_write(_A.UC_ARM_REG_IPSR, 0)
            if not self._m_manual_bank:
                control = self._uc.reg_read(_A.UC_ARM_REG_CONTROL)
                control = (control | 2) if return_psp else (control & ~2)
                self._uc.reg_write(_A.UC_ARM_REG_CONTROL, control)
        elif frame[7] & 0x1FF:
            # Returning to handler mode: put the preempted exception number
            # back. Guarded on the stacked value being non-zero, because RTOS
            # ports synthesise exception frames (ChibiOS' _port_irq_epilogue
            # builds one carrying only the T bit) and writing 0 there would
            # drop a running handler into thread mode -- the opposite mistake.
            self._uc.reg_write(_A.UC_ARM_REG_IPSR, frame[7] & 0x1FF)
        # ICSR follows the mode change: VECTACTIVE is the exception we are
        # returning TO (0 in thread mode).
        self._icsr_exit(0 if return_thread else (frame[7] & 0x1FF))
        self.write_register("pc", frame[6])
        log.info("exc_return %#x: popped from %s, resuming at 0x%x",
                 addr, "PSP" if return_psp else "MSP", frame[6])
        # A PendSV requested from the handler we're leaving tail-chains here.
        if return_thread and getattr(self, "_pendsv_pending", False):
            self._pendsv_pending = False
            self._pending_irqs.append(-2)      # -2 -> exception 14 (PendSV)
        # Unicorn needs to restart from the restored PC; stop the current
        # emu_start. Flag this as an exception-return stop so cont() resumes
        # from the restored PC itself, rather than surfacing the internal stop
        # to the dispatch loop (which would re-dispatch a breakpoint the frame
        # happened to return onto — defeating _skip_bp_once and livelocking an
        # observe-only tick handler — or exit outright when no IRQ controller is
        # configured, e.g. a native SVCall/PendSV-launched RTOS first task).
        self._exc_return_pending = True
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
