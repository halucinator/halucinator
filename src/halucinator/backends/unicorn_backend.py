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
    _HAVE_UNICORN = True
except ImportError:
    _HAVE_UNICORN = False
    unicorn = None  # type: ignore[assignment]
    arm_const = None  # type: ignore[assignment]
    arm64_const = None  # type: ignore[assignment]
    mips_const = None  # type: ignore[assignment]
    ppc_const = None  # type: ignore[assignment]


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
    "arm":            ("arm",    "thumb", True,  False, 4),
    "arm64":          ("arm64",  "arm",   False, False, 8),
    "mips":           ("mips",   "mips32_be", False, True, 4),
    "powerpc":        ("ppc",    "ppc32_be", False, True, 4),
    "powerpc:MPC8XX": ("ppc",    "ppc32_be", False, True, 4),
    "ppc64":          ("ppc",    "ppc64_be", False, True, 8),
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
        self._breakpoints: Dict[int, int] = {}   # addr → bp_id
        self._bp_callbacks: Dict[int, Callable] = {}  # bp_id → callback

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

    def _intr_hook(self, uc, intno, user_data):
        try:
            pc = self.read_register("pc")
        except Exception:
            pc = -1
        log.error("UnicornBackend: CPU exception/interrupt %d at pc=0x%x",
                  intno, pc)
        uc.emu_stop()

    def _invalid_mem_hook(self, uc, access, addr, size, value, user_data):
        """Intercept invalid memory accesses. On cortex-m, a fetch from
        an EXC_RETURN magic address is the ISR returning — we unwind
        the pushed exception frame and resume at the saved PC. Other
        invalid accesses are logged and the emulator aborts."""
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
        log.error("UnicornBackend: unmapped %s at 0x%x (size %d, value 0x%x) "
                  "from pc=0x%x", kind, addr, size, value, pc)
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
            self._stopped = True
            self._bp_hit_addr = pc
            uc.emu_stop()

    # ------------------------------------------------------------------
    # Memory
    # ------------------------------------------------------------------

    def add_memory_region(self, region: MemoryRegion) -> None:
        self._regions.append(region)
        if self._uc is not None:
            self._map_region(region)

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
        pc = self.read_register("pc")
        # Unicorn Thumb mode needs the LSB set on the start address.
        start = (pc | 1) if self._is_thumb else pc
        # Cap emu_start upper bound by arch word size.
        until = (1 << (self._word_size * 8)) - 1
        try:
            self._uc.emu_start(start, until, timeout=0, count=0)
        except unicorn.UcError:
            if self._stopped:
                pass  # stopped by breakpoint — normal
            else:
                raise

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

    def inject_irq(self, irq_num: int) -> None:
        """Deliver an external IRQ to a cortex-m CPU. Pushes a minimal
        exception frame (r0–r3, r12, lr, pc, xpsr) onto the main stack,
        sets LR to the thread-mode / MSP EXC_RETURN magic, and sets PC
        to the ISR address from the vector table.

        Other archs don't have a comparable in-process interrupt model —
        use QEMUBackend or Avatar2Backend for those.
        """
        if self.arch_name != "cortex-m3":
            log.warning(
                "UnicornBackend.inject_irq(%d): only cortex-m3 has an "
                "in-process IRQ model today. Arch=%s is ignored.",
                irq_num, self.arch_name,
            )
            return
        if self._uc is None:
            raise RuntimeError("Call UnicornBackend.init() first")

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
