"""
HalBackend — abstract base class that every emulator backend must implement.

Layer 1: raw emulation contract (memory, registers, control, memory regions).
Layer 2: HalTarget (below) adds ABI-aware helpers built on top of these primitives.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple, Union


# ---------------------------------------------------------------------------
# Memory region types
# ---------------------------------------------------------------------------

class MemoryRegion:
    """Descriptor for a memory region passed to HalBackend.add_memory."""

    def __init__(
        self,
        name: str,
        base_addr: int,
        size: int,
        permissions: str = "rwx",
        file: Optional[str] = None,
        emulate: Optional[Any] = None,
        qemu_name: Optional[str] = None,
        qemu_properties: Optional[List[Dict]] = None,
        read_hook: Optional[Callable] = None,
        write_hook: Optional[Callable] = None,
    ):
        self.name = name
        self.base_addr = base_addr
        self.size = size
        self.permissions = permissions
        self.file = file
        self.emulate = emulate
        self.qemu_name = qemu_name
        self.qemu_properties = qemu_properties or []
        self.read_hook = read_hook
        self.write_hook = write_hook


# ---------------------------------------------------------------------------
# Abstract backend
# ---------------------------------------------------------------------------

class HalBackend(ABC):
    """
    Abstract base class for all HALucinator emulator backends.

    Concrete implementations: Avatar2Backend, QEMUBackend, UnicornBackend.
    """

    # ------------------------------------------------------------------
    # Memory operations  (must implement)
    # ------------------------------------------------------------------

    @abstractmethod
    def read_memory(self, addr: int, size: int, num_words: int = 1,
                    raw: bool = False) -> Union[int, bytes]:
        """Read *num_words* of *size* bytes each from *addr*.
        Returns int when raw=False and num_words=1, otherwise bytes."""

    @abstractmethod
    def write_memory(self, addr: int, size: int,
                     value: Union[int, bytes, bytearray],
                     num_words: int = 1, raw: bool = False) -> bool:
        """Write *value* to *addr*."""

    # ------------------------------------------------------------------
    # Register operations  (must implement)
    # ------------------------------------------------------------------

    @abstractmethod
    def read_register(self, register: str) -> int:
        """Return the current value of *register* (by name)."""

    @abstractmethod
    def write_register(self, register: str, value: int) -> None:
        """Set *register* to *value*."""

    # ------------------------------------------------------------------
    # Execution control  (must implement)
    # ------------------------------------------------------------------

    @abstractmethod
    def set_breakpoint(self, addr: int, hardware: bool = False,
                       temporary: bool = False) -> int:
        """Set a breakpoint at *addr*; return an opaque bp_id."""

    @abstractmethod
    def remove_breakpoint(self, bp_id: int) -> None:
        """Remove the breakpoint identified by *bp_id*."""

    def set_watchpoint(self, addr: int, write: bool = True,
                       read: bool = False) -> int:
        """Set a watchpoint. Default: raise if unsupported."""
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support watchpoints"
        )

    @abstractmethod
    def cont(self, blocking: bool = True) -> None:
        """Resume execution."""

    @abstractmethod
    def stop(self) -> None:
        """Pause execution."""

    def step(self) -> None:
        """Single-step one instruction. Default: raise if unsupported."""
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support single-step"
        )

    # ------------------------------------------------------------------
    # Memory-region setup  (must implement)
    # ------------------------------------------------------------------

    @abstractmethod
    def add_memory_region(self, region: MemoryRegion) -> None:
        """Register a memory region with the backend before starting."""

    # ------------------------------------------------------------------
    # Optional extensions
    # ------------------------------------------------------------------

    def inject_irq(self, irq_num: int) -> None:
        """Inject interrupt *irq_num*. Not all backends support this."""
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support inject_irq"
        )

    def shutdown(self) -> None:
        """Tear down the backend. Override if cleanup is needed."""

    def list_registers(self) -> List[str]:
        """Return the list of architectural register names this backend
        exposes for read/write. Primarily used by the state recorder to
        snapshot CPU state at breakpoints. Default: derive from the
        concrete ABI mixin if one is bound, else return a conservative
        ARM list."""
        abi = getattr(self, "_abi", None)
        if abi is not None and hasattr(abi, "REGISTERS"):
            return list(abi.REGISTERS)
        # Fallback — conservative ARM32 set.
        return [f"r{i}" for i in range(13)] + ["sp", "lr", "pc"]

    # ------------------------------------------------------------------
    # Convenience wrappers (implemented once, reused by all backends)
    # ------------------------------------------------------------------

    @property
    def regs(self) -> "_RegsProxy":
        """Attribute-style register access: backend.regs.pc, backend.regs.r0 = 5, etc."""
        proxy = self.__dict__.get("_regs_proxy")
        if proxy is None:
            proxy = _RegsProxy(self)
            self.__dict__["_regs_proxy"] = proxy
        return proxy

    def read_memory_word(self, addr: int) -> int:
        return self.read_memory(addr, 4, 1)

    def read_memory_bytes(self, addr: int, size: int) -> bytes:
        return self.read_memory(addr, 1, size, raw=True)

    def write_memory_word(self, addr: int, value: int) -> bool:
        return self.write_memory(addr, 4, value)

    def write_memory_bytes(self, addr: int, value: bytes) -> bool:
        return self.write_memory(addr, 1, value, len(value), raw=True)


# ---------------------------------------------------------------------------
# Register proxy: lets handlers write backend.regs.pc = x instead of
# backend.write_register("pc", x). Mirrors avatar2 QemuTarget.regs ergonomics.
# ---------------------------------------------------------------------------

class _RegsProxy:
    __slots__ = ("_backend",)

    def __init__(self, backend: "HalBackend"):
        object.__setattr__(self, "_backend", backend)

    def __getattr__(self, name: str) -> int:
        return self._backend.read_register(name)

    def __setattr__(self, name: str, value: int) -> None:
        if name == "_backend":
            object.__setattr__(self, name, value)
        else:
            self._backend.write_register(name, value)


# ---------------------------------------------------------------------------
# ABI mixins  (calling-convention helpers implemented once per ABI)
# ---------------------------------------------------------------------------

class _ABIBase:
    """
    Base ABI mixin providing the read_string helper that works for all archs.
    Requires: read_memory.
    """
    WORD_SIZE: int = 4

    def read_string(self, addr: int, max_len: int = 256) -> str:
        raw = bytes(self.read_memory(addr, 1, max_len, raw=True))
        return raw.decode("latin-1").split("\x00")[0]


class ARM32HalMixin(_ABIBase):
    """
    ARM32 / Cortex-M ABI: args in r0–r3 then stack, return addr in lr,
    return value in r0.
    """
    WORD_SIZE = 4
    REGISTERS = tuple(f"r{i}" for i in range(13)) + ("sp", "lr", "pc", "cpsr")

    def get_arg(self, idx: int) -> int:
        if idx < 0:
            raise ValueError(f"Argument index must be non-negative, got {idx}")
        if idx < 4:
            return self.read_register(f"r{idx}")
        sp = self.read_register("sp")
        return self.read_memory(sp + (idx - 4) * 4, 4, 1)

    def set_args(self, args: List[int]) -> None:
        for i, v in enumerate(args[:4]):
            self.write_register(f"r{i}", v)
        if len(args) > 4:
            sp = self.read_register("sp")
            for i, v in enumerate(args[4:]):
                sp -= 4
                self.write_memory(sp, 4, v)
            self.write_register("sp", sp)

    def get_ret_addr(self) -> int:
        return self.read_register("lr")

    def set_ret_addr(self, ret_addr: int) -> None:
        self.write_register("lr", ret_addr)

    def execute_return(self, ret_value: int) -> None:
        if ret_value is not None:
            self.write_register("r0", ret_value)
        self.write_register("pc", self.read_register("lr"))
        self.cont()


# Back-compat alias — existing callers use ARMHalMixin.
ARMHalMixin = ARM32HalMixin


class ARM64HalMixin(_ABIBase):
    """
    AArch64 ABI: args in x0–x7 then stack, return addr in x30 (lr),
    return value in x0.
    """
    WORD_SIZE = 8
    REGISTERS = tuple(f"x{i}" for i in range(31)) + ("sp", "pc")

    def get_arg(self, idx: int) -> int:
        if idx < 0:
            raise ValueError(f"Argument index must be non-negative, got {idx}")
        if idx < 8:
            return self.read_register(f"x{idx}")
        sp = self.read_register("sp")
        return self.read_memory(sp + (idx - 8) * 8, 8, 1)

    def set_args(self, args: List[int]) -> None:
        for i, v in enumerate(args[:8]):
            self.write_register(f"x{i}", v)
        if len(args) > 8:
            sp = self.read_register("sp")
            for v in args[:7:-1]:
                sp -= 8
                self.write_memory(sp, 8, v)
            self.write_register("sp", sp)

    def get_ret_addr(self) -> int:
        return self.read_register("x30")

    def set_ret_addr(self, ret_addr: int) -> None:
        self.write_register("x30", ret_addr)

    def execute_return(self, ret_value: int) -> None:
        if ret_value is not None:
            self.write_register("x0", ret_value)
        self.write_register("pc", self.read_register("x30"))
        self.cont()


class MIPSHalMixin(_ABIBase):
    """
    MIPS32 O32 ABI: args in a0–a3 then stack, return addr in ra,
    return value in v0.
    """
    WORD_SIZE = 4
    REGISTERS = (
        "zero", "at", "v0", "v1", "a0", "a1", "a2", "a3",
        "t0", "t1", "t2", "t3", "t4", "t5", "t6", "t7",
        "s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7",
        "t8", "t9", "k0", "k1", "gp", "sp", "fp", "ra", "pc",
    )

    def get_arg(self, idx: int) -> int:
        if idx < 0:
            raise ValueError(f"Argument index must be non-negative, got {idx}")
        if idx < 4:
            return self.read_register(f"a{idx}")
        sp = self.read_register("sp")
        return self.read_memory(sp + (idx - 4) * 4, 4, 1)

    def set_args(self, args: List[int]) -> None:
        for i, v in enumerate(args[:4]):
            self.write_register(f"a{i}", v)
        if len(args) > 4:
            sp = self.read_register("sp")
            for i, v in enumerate(args[4:]):
                self.write_memory(sp + (4 + i) * 4, 4, v)

    def get_ret_addr(self) -> int:
        return self.read_register("ra")

    def set_ret_addr(self, ret_addr: int) -> None:
        self.write_register("ra", ret_addr)

    def execute_return(self, ret_value: int) -> None:
        if ret_value is not None:
            self.write_register("v0", ret_value & 0xFFFFFFFF)
        self.write_register("pc", self.read_register("ra"))
        self.cont()


class PowerPCHalMixin(_ABIBase):
    """
    PowerPC ABI: args in r3–r10 then stack, return addr in lr,
    return value in r3.
    """
    WORD_SIZE = 4
    REGISTERS = tuple(f"r{i}" for i in range(32)) + ("pc", "lr", "ctr", "msr", "xer", "cr")

    def get_arg(self, idx: int) -> int:
        if idx < 0:
            raise ValueError(f"Argument index must be non-negative, got {idx}")
        if idx < 8:
            return self.read_register(f"r{idx + 3}")
        sp = self.read_register("sp")
        return self.read_memory(sp + (idx - 8) * 4, 4, 1)

    def set_args(self, args: List[int]) -> None:
        for i, v in enumerate(args[:8]):
            self.write_register(f"r{i + 3}", v)

    def get_ret_addr(self) -> int:
        return self.read_register("lr")

    def set_ret_addr(self, ret_addr: int) -> None:
        self.write_register("lr", ret_addr)

    def execute_return(self, ret_value: int) -> None:
        if ret_value is not None:
            self.write_register("r3", ret_value & 0xFFFFFFFF)
        self.write_register("pc", self.read_register("lr"))
        self.cont()


class PowerPC64HalMixin(PowerPCHalMixin):
    """PPC64 ABI — same register conventions as PPC32 but 8-byte words."""
    WORD_SIZE = 8

    def get_arg(self, idx: int) -> int:
        if idx < 0:
            raise ValueError(f"Argument index must be non-negative, got {idx}")
        if idx < 8:
            return self.read_register(f"r{idx + 3}")
        sp = self.read_register("sp")
        return self.read_memory(sp + (idx - 8) * 8, 8, 1)

    def execute_return(self, ret_value: int) -> None:
        if ret_value is not None:
            self.write_register("r3", ret_value & 0xFFFFFFFFFFFFFFFF)
        self.write_register("pc", self.read_register("lr"))
        self.cont()


# Map halucinator arch strings → the mixin class that provides calling
# conventions. QEMUBackend/UnicornBackend/others look this up to pick
# the right ABI at instantiation time.
ABI_MIXINS: Dict[str, type] = {
    "cortex-m3": ARM32HalMixin,
    "arm":       ARM32HalMixin,
    "arm64":     ARM64HalMixin,
    "mips":      MIPSHalMixin,
    "powerpc":   PowerPCHalMixin,
    "powerpc:MPC8XX": PowerPCHalMixin,
    "ppc64":     PowerPC64HalMixin,
}
