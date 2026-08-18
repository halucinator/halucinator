"""
HalBackend — abstract base class that every emulator backend must implement.

Layer 1: raw emulation contract (memory, registers, control, memory regions).
Layer 2: HalTarget (below) adds ABI-aware helpers built on top of these primitives.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple, Union

log = logging.getLogger(__name__)


def log_snapshot_mismatch(backend: "HalBackend", snap: "Snapshot",
                          field_name: str) -> None:
    """Log why ``restore_state`` refused a snapshot (incompatible field)."""
    log.error("%s.restore_state: refusing snapshot — %s mismatch "
              "(snapshot=%r, expected=%r)",
              backend.__class__.__name__, field_name,
              getattr(snap, field_name, None),
              (backend.__class__.__name__ if field_name == "backend_type"
               else backend.SNAPSHOT_VERSION))


# ---------------------------------------------------------------------------
# Snapshot types (Layer 1 — see halucinator.snapshot for the coordinator)
# ---------------------------------------------------------------------------

class SnapshotError(RuntimeError):
    """Raised by ``save_state`` when a complete snapshot cannot be captured.

    Save is all-or-nothing: rather than return a half-captured ``Snapshot``
    (which would silently corrupt the guest on restore), the backend raises
    and the partial capture is discarded.
    """


@dataclass
class Snapshot:
    """A Layer-1 backend checkpoint.

    Tagged with ``backend_type`` (the concrete backend class name) and a schema
    ``version`` so ``restore_state`` can reject an incompatible snapshot and
    return ``False`` instead of corrupting the guest. ``data`` is backend-
    private (generic fallback: ``{"regs": ..., "mem": ...}``; Unicorn native:
    a ``UcContext`` + region blobs). A snapshot may own resources, so it is a
    context manager and exposes :meth:`release`.
    """

    backend_type: str
    version: int
    data: Any = None
    _released: bool = field(default=False, repr=False)

    def release(self) -> None:
        """Free any resources this snapshot holds. Idempotent."""
        if self._released:
            return
        self.data = None
        self._released = True

    def __enter__(self) -> "Snapshot":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.release()


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

    def _bind_abi(self, arch: str) -> None:
        """Bind the arch-specific ABI mixin's helpers onto this instance.

        ARM32 stays the default via class inheritance, so the backend is
        usable without __init__ (e.g. in unit tests); any other arch has its
        ``get_arg``/``set_args``/``get_ret_addr``/``set_ret_addr``/
        ``execute_return``/``read_string`` overridden by the mixin's bound
        methods. Shared by every backend that selects an ABI at init time.
        """
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
        """Inject interrupt *irq_num*.

        The base implementation routes through the configured
        IrqController (NVIC / GIC / MIPS Cause / OpenPIC). Backends
        that have a faster or more accurate native path (e.g. avatar2
        + qemu's `avatar-armv7m-inject-irq` QMP command on Cortex-M)
        override this method to short-circuit, and fall back to
        super().inject_irq for ISAs the native path doesn't cover.
        """
        controller = getattr(self, "_irq_controller", None)
        if controller is None:
            from halucinator.backends.irq import IrqConfigError
            raise IrqConfigError(
                f"{self.__class__.__name__}.inject_irq: no interrupt "
                f"controller configured. Declare one in the YAML "
                f"machine.interrupt_controller block, or use a backend "
                f"that overrides inject_irq for this arch."
            )
        controller.trigger(self, irq_num)

    def set_irq_controller(self, controller: Any) -> None:
        """Attach an IrqController instance. Called by main.py after
        the backend is constructed and the YAML config has been parsed."""
        self._irq_controller = controller

    def set_delivery_plan(self, plan: Any) -> None:
        """Attach the CPU-exception DeliveryPlan (the 'where-to-land'
        data). Only consulted by in-process backends whose CPU model
        doesn't take exceptions natively; harmless on QEMU/avatar2."""
        self._delivery_plan = plan

    def set_exception_deliverer(self, deliverer: Any) -> None:
        """Attach the per-arch ExceptionDeliverer. Backends that take
        exceptions natively never read this."""
        self._exception_deliverer = deliverer

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
    # Snapshot / restore  (Layer 1)
    #
    # The generic implementation below dumps every writable memory region
    # plus the full register file and restores them via the primitive
    # read/write methods — universal but slow. Fast backends (Unicorn)
    # override save_state/restore_state with a native path; the coordinator
    # in halucinator.snapshot bundles this with Layer-2 peripheral state.
    # ------------------------------------------------------------------

    SNAPSHOT_VERSION = 1

    def can_snapshot(self) -> bool:
        """Whether this backend can produce a snapshot. Always True for the
        generic fallback; a backend with no usable path overrides to False."""
        return True

    def snapshot_is_fast(self) -> bool:
        """Hint for the consumer: is save/restore cheap enough for an
        inner loop? The generic register+RAM dump is not — override to True on
        a native ms-scale path (Unicorn)."""
        return False

    def _snapshot_regions(self) -> List["MemoryRegion"]:
        """Writable regions the generic snapshot must capture.

        Skips read-only flash (never changes) AND ``emulate=``-backed MMIO
        regions: those are forwarded to a Python peripheral model, so their
        state is Layer 2 — reading them here would invoke the model's
        hw_read (not real RAM) and double-capture what the peripheral
        registry already owns."""
        regions = getattr(self, "_regions", None) or []
        return [r for r in regions
                if "w" in r.permissions and not getattr(r, "emulate", None)]

    def save_state(self, portable: bool = False) -> "Snapshot":
        """Capture registers + writable RAM via the primitive read methods.

        ``portable=True`` requests a snapshot made of plain python values,
        safe to pickle and restore in a different process (what disk
        persistence needs). The generic path here is ALWAYS portable — the
        flag exists for backends whose fast path holds process-local native
        handles (unicorn's context blob) and must capture differently.

        Raises :class:`SnapshotError` if there is nothing to capture (no
        writable regions) so a caller never gets an empty, useless snapshot.

        Registers are captured tolerantly: a name in ``list_registers()`` that
        a given backend's stub can't actually read (register sets vary across
        GDB stubs / emulators) is skipped with a warning rather than aborting
        the whole snapshot. Memory is strict — a failed region read raises,
        because missing RAM means the snapshot is not a faithful resume point.
        """
        regions = self._snapshot_regions()
        if not regions:
            raise SnapshotError(
                f"{self.__class__.__name__}.save_state: no writable memory "
                f"regions to capture")
        regs = {}
        for name in self.list_registers():
            try:
                regs[name] = self.read_register(name)
            except Exception as exc:  # noqa: BLE001
                log.warning("%s.save_state: register %r not readable; "
                            "skipped (%s)", self.__class__.__name__, name, exc)
        try:
            mem = [(r.base_addr,
                    bytes(self.read_memory(r.base_addr, 1, r.size, raw=True)))
                   for r in regions]
        except Exception as exc:  # noqa: BLE001
            raise SnapshotError(
                f"{self.__class__.__name__}.save_state failed reading "
                f"memory: {exc!r}") from exc
        return Snapshot(backend_type=self.__class__.__name__,
                        version=self.SNAPSHOT_VERSION,
                        data={"regs": regs, "mem": mem})

    def restore_state(self, snap: "Snapshot") -> bool:
        """Restore a snapshot produced by :meth:`save_state`.

        Validates ``backend_type`` and ``version`` BEFORE touching any state,
        returning ``False`` (no mutation) on mismatch rather than corrupting
        the guest. Returns ``True`` once registers + memory are restored, and
        ``False`` if a memory write reports failure (a half-restore must be
        reported, not silently swallowed — that is the whole-or-nothing
        contract the coordinator relies on).
        """
        if snap.backend_type != self.__class__.__name__:
            log_snapshot_mismatch(self, snap, "backend_type")
            return False
        if snap.version != self.SNAPSHOT_VERSION:
            log_snapshot_mismatch(self, snap, "version")
            return False
        data = snap.data or {}
        for name, value in data.get("regs", {}).items():
            # Tolerant, symmetric with save_state: a register this stub won't
            # accept a write for is skipped with a warning, not fatal.
            try:
                self.write_register(name, value)
            except Exception as exc:  # noqa: BLE001
                log.warning("%s.restore_state: register %r not writable; "
                            "skipped (%s)", self.__class__.__name__, name, exc)
        for base, blob in data.get("mem", []):
            # write_memory returns False on a rejected write (unmapped/prot);
            # propagate it so the caller knows the machine is now inconsistent.
            if self.write_memory(base, 1, blob, len(blob), raw=True) is False:
                log.error("%s.restore_state: write_memory(0x%x, %d bytes) "
                          "failed; machine is now half-restored",
                          self.__class__.__name__, base, len(blob))
                return False
        return True

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

    def write_registers(self, regs: Dict[str, int]) -> None:
        """Write several registers. Default: one write_register call each.
        Backends with a faster batched path (e.g. QEMUBackend collapsing them
        into a single GDB round-trip) override this. Used by execute_return to
        set the return value and pc together."""
        for name, value in regs.items():
            self.write_register(name, value)


class ARM32HalMixin(_ABIBase):
    """
    ARM32 / Cortex-M ABI: args in r0–r3 then stack, return addr in lr,
    return value in r0.

    AAPCS reserves no home space for the register-passed arguments, so at the
    callee's entry the fifth argument is the word AT the stack pointer, the
    sixth is at sp+4, and so on — ascending. ``get_arg`` and ``set_args`` below
    both index from that base.
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
            extra = args[4:]
            # Allocate the whole outgoing block, then fill it ASCENDING so the
            # fifth argument lands at the FINAL sp — which is where get_arg
            # looks for it.
            #
            # This used to push one word at a time (`sp -= 4` then write), which
            # placed the fifth argument at the HIGHEST address and the last at
            # the lowest. The pair therefore round-tripped reversed: set_args
            # [a,b,c,d,e,f] then get_arg(4) returned f, not e. Silent, and only
            # visible on a call with more than four arguments.
            #
            # The block is rounded up to 8 bytes because AAPCS requires SP to be
            # 8-byte aligned at a public interface; the padding sits ABOVE the
            # arguments so the fifth stays at sp+0.
            size = ((4 * len(extra)) + 7) & ~7
            sp = (self.read_register("sp") - size) & 0xFFFFFFFF
            for i, v in enumerate(extra):
                self.write_memory(sp + i * 4, 4, v)
            self.write_register("sp", sp)

    def get_ret_addr(self) -> int:
        return self.read_register("lr")

    def set_ret_addr(self, ret_addr: int) -> None:
        self.write_register("lr", ret_addr)

    def execute_return(self, ret_value: int) -> None:
        regs = {"pc": self.read_register("lr")}
        if ret_value is not None:
            regs["r0"] = ret_value & 0xFFFFFFFF
        self.write_registers(regs)
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
        regs = {"pc": self.read_register("x30")}
        if ret_value is not None:
            regs["x0"] = ret_value & 0xFFFFFFFFFFFFFFFF
        self.write_registers(regs)
        self.cont()


class MIPSHalMixin(_ABIBase):
    """
    MIPS32 O32 ABI: args in a0–a3 then stack, return addr in ra,
    return value in v0.

    O32 reserves a 16-byte "argument slot" area at the TOP of the caller's
    frame -- $sp+0 through $sp+12 -- as home space for a0-a3, even though
    those four are passed in registers and the callee usually never spills
    them. The fifth argument is therefore at $sp+16, not at $sp+0. Both
    ``get_arg`` and ``set_args`` below index from that base; an intercept
    reading at $sp would get the a0 home slot (typically stale or zero)
    instead of the argument it asked for.
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
        # $sp + idx*4, NOT $sp + (idx-4)*4: argument 5 (idx 4) sits above the
        # 16-byte a0-a3 home space, at $sp+16. This is the same address
        # set_args writes, so a set/get round-trip agrees.
        sp = self.read_register("sp")
        return self.read_memory(sp + idx * 4, 4, 1)

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
        regs = {"pc": self.read_register("ra")}
        if ret_value is not None:
            regs["v0"] = ret_value & 0xFFFFFFFF
        self.write_registers(regs)
        self.cont()


class TriCoreHalMixin(_ABIBase):
    """
    Infineon TriCore EABI: data args in d4-d7 (address args in a4-a7), then the
    stack; return address in a11 (RA); return value in d2.

    TriCore splits its register file in two: 16 *data* registers (d0-d15) and 16
    *address* registers (a0-a15). The ABI assigns them separately -- an integer
    argument goes in d4..d7 while a pointer goes in a4..a7 -- so ``get_arg``
    cannot be a single index into one bank. We return the DATA register, which
    is what an intercept reading a scalar argument wants; a handler needing the
    pointer argument reads ``a4``..``a7`` directly via ``read_register``.

    Two more TriCore-specific facts matter to callers:
      * ``a10`` is the stack pointer (SP) and ``a11`` is the return address
        (RA), written by ``call``; there is no separate ``lr``.
      * ``a0``/``a1``/``a8``/``a9`` are *system-global* registers, preserved
        across calls and normally set up once at boot -- do not clobber them.
    """
    WORD_SIZE = 4
    REGISTERS = (tuple(f"d{i}" for i in range(16))
                 + tuple(f"a{i}" for i in range(16))
                 + ("pc", "psw", "pcxi", "fcx", "lcx", "sp", "ra"))

    def get_arg(self, idx: int) -> int:
        if idx < 0:
            raise ValueError(f"Argument index must be non-negative, got {idx}")
        if idx < 4:
            return self.read_register(f"d{4 + idx}")
        # Overflow arguments start AT the stack pointer. TriCore's EABI has no
        # O32-style home space: the caller does not reserve slots for the
        # register-passed arguments, so the fifth argument is the first word of
        # the outgoing area. (This is where a mixin copied from MIPSHalMixin
        # goes wrong -- MIPS reserves 16 bytes for a0-a3 and TriCore does not.)
        sp = self.read_register("a10")
        return self.read_memory(sp + (idx - 4) * 4, 4, 1)

    def set_args(self, args: List[int]) -> None:
        for i, v in enumerate(args[:4]):
            self.write_register(f"d{4 + i}", v)
        if len(args) > 4:
            sp = self.read_register("a10")
            for i, v in enumerate(args[4:]):
                # Same address get_arg reads back. This previously wrote at
                # sp+16.. -- the MIPS O32 home-space offset, inherited by
                # copy -- so a set/get round-trip disagreed by 16 bytes and an
                # intercept reading argument 5 got an unrelated word.
                self.write_memory(sp + i * 4, 4, v)

    def get_ret_addr(self) -> int:
        return self.read_register("a11")

    def set_ret_addr(self, ret_addr: int) -> None:
        self.write_register("a11", ret_addr)

    def execute_return(self, ret_value: int) -> None:
        regs = {"pc": self.read_register("a11")}
        if ret_value is not None:
            regs["d2"] = ret_value & 0xFFFFFFFF
        self.write_registers(regs)
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
        regs = {"pc": self.read_register("lr")}
        if ret_value is not None:
            regs["r3"] = ret_value & 0xFFFFFFFF
        self.write_registers(regs)
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
        regs = {"pc": self.read_register("lr")}
        if ret_value is not None:
            regs["r3"] = ret_value & 0xFFFFFFFFFFFFFFFF
        self.write_registers(regs)
        self.cont()


class X86HalMixin(_ABIBase):
    """
    32-bit x86 / i386 System V cdecl ABI: all args on the stack, the
    return address is the word at [esp], the return value is in eax.

    Stack layout at function entry (after the `call` pushed the return
    address):  [esp] = return addr, [esp+4] = arg0, [esp+8] = arg1, ...
    """
    WORD_SIZE = 4
    REGISTERS = (
        "eax", "ebx", "ecx", "edx", "esi", "edi", "ebp", "esp",
        "eip", "eflags", "cs", "ds", "es", "fs", "gs", "ss",
    )

    def get_arg(self, idx: int) -> int:
        if idx < 0:
            raise ValueError(f"Argument index must be non-negative, got {idx}")
        sp = self.read_register("esp")
        return self.read_memory(sp + (idx + 1) * 4, 4, 1)

    def set_args(self, args: List[int]) -> None:
        # cdecl: caller pushes args right-to-left. We write them above the
        # current return address without moving esp (callee reads them in
        # place); the caller is responsible for stack cleanup.
        sp = self.read_register("esp")
        for i, v in enumerate(args):
            self.write_memory(sp + (i + 1) * 4, 4, v)

    def get_ret_addr(self) -> int:
        sp = self.read_register("esp")
        return self.read_memory(sp, 4, 1)

    def set_ret_addr(self, ret_addr: int) -> None:
        sp = self.read_register("esp")
        self.write_memory(sp, 4, ret_addr)

    def execute_return(self, ret_value: int) -> None:
        # Emulate `ret`: pop the return address and jump to it.
        sp = self.read_register("esp")
        ret_addr = self.read_memory(sp, 4, 1)
        regs = {"esp": sp + 4, "pc": ret_addr}
        if ret_value is not None:
            regs["eax"] = ret_value & 0xFFFFFFFF
        self.write_registers(regs)
        self.cont()


class RISCVHalMixin(_ABIBase):
    """
    RV32 ILP32 ABI: args in a0–a7 (x10–x17) then stack, return addr in ra
    (x1), return value in a0 (x10). x0 is the hardwired-zero register.
    """
    WORD_SIZE = 4
    REGISTERS = (
        "zero", "ra", "sp", "gp", "tp", "t0", "t1", "t2",
        "s0", "s1", "a0", "a1", "a2", "a3", "a4", "a5", "a6", "a7",
        "s2", "s3", "s4", "s5", "s6", "s7", "s8", "s9", "s10", "s11",
        "t3", "t4", "t5", "t6", "pc",
    )

    def get_arg(self, idx: int) -> int:
        if idx < 0:
            raise ValueError(f"Argument index must be non-negative, got {idx}")
        if idx < 8:
            return self.read_register(f"a{idx}")
        sp = self.read_register("sp")
        return self.read_memory(sp + (idx - 8) * 4, 4, 1)

    def set_args(self, args: List[int]) -> None:
        for i, v in enumerate(args[:8]):
            self.write_register(f"a{i}", v)
        if len(args) > 8:
            sp = self.read_register("sp")
            for i, v in enumerate(args[8:]):
                self.write_memory(sp + i * 4, 4, v)

    def get_ret_addr(self) -> int:
        return self.read_register("ra")

    def set_ret_addr(self, ret_addr: int) -> None:
        self.write_register("ra", ret_addr)

    def execute_return(self, ret_value: int) -> None:
        regs = {"pc": self.read_register("ra")}
        if ret_value is not None:
            regs["a0"] = ret_value & 0xFFFFFFFF
        self.write_registers(regs)
        self.cont()


class SPARCHalMixin(_ABIBase):
    """
    SPARC V8 (Gaisler LEON) ABI: the first six arguments arrive in %o0-%o5,
    further arguments on the stack, the return value goes back in %o0, and the
    return address derives from %o7.

    Two SPARC-specific facts drive every method here:

      * **%o7 is not a return address, it is the address of the `call`.**
        SPARC's `call` stores its own PC in %o7 and the callee returns with
        `jmpl %o7+8` (`retl`, leaf) or `jmpl %i7+8` (`ret`, after a `save`) --
        the +8 steps over the call AND its delay slot. Treating %o7 as the
        resume address sends control back to the call instruction, which
        re-invokes the interposed function forever. So get/set_ret_addr and
        execute_return all carry the +8/-8 bias.

      * **Stack arguments start at %sp+92, not %sp.** A SPARC frame reserves
        64 bytes for the register-window spill area, 4 for the aggregate-return
        pointer and 24 of home space for %o0-%o5 before the seventh argument
        appears. Reading at %sp would return the callee's saved window.

    Intercepts fire at the callee's first instruction, i.e. BEFORE its `save`,
    so the caller's window is still current and %o0-%o5/%o7 are the registers
    to read. After a `save` the same values are addressed as %i0-%i5/%i7.
    """
    WORD_SIZE = 4
    REGISTERS = (
        tuple(f"g{i}" for i in range(8))
        + tuple(f"o{i}" for i in range(8))
        + tuple(f"l{i}" for i in range(8))
        + tuple(f"i{i}" for i in range(8))
        + ("pc", "sp", "fp", "y")
    )

    # NOTE: the 92-byte stack-argument bias below is written as a literal in
    # both methods rather than a class constant. ``_bind_abi`` copies only the
    # six ABI *methods* onto the backend instance -- class attributes of the
    # mixin never come with them -- so ``self.<constant>`` would raise
    # AttributeError on the backend at the first call. Every mixin in this file
    # inlines its constants for the same reason.

    def get_arg(self, idx: int) -> int:
        if idx < 0:
            raise ValueError(f"Argument index must be non-negative, got {idx}")
        if idx < 6:
            return self.read_register(f"o{idx}")
        # 64-byte window save area + 4-byte aggregate return slot + 24 bytes
        # of home space for %o0-%o5, then the seventh argument.
        sp = self.read_register("sp")
        return self.read_memory(sp + 92 + (idx - 6) * 4, 4, 1)

    def set_args(self, args: List[int]) -> None:
        for i, v in enumerate(args[:6]):
            self.write_register(f"o{i}", v)
        if len(args) > 6:
            sp = self.read_register("sp")
            for i, v in enumerate(args[6:]):
                # Same expression get_arg reads back, so a set/get round-trip
                # agrees -- unlike the mips/tricore mixins, whose writer and
                # reader disagree by one home-space block.
                self.write_memory(sp + 92 + i * 4, 4, v)

    def get_ret_addr(self) -> int:
        return (self.read_register("o7") + 8) & 0xFFFFFFFF

    def set_ret_addr(self, ret_addr: int) -> None:
        self.write_register("o7", (ret_addr - 8) & 0xFFFFFFFF)

    def execute_return(self, ret_value: int) -> None:
        # Emulate `retl`: resume at %o7+8, past the call and its delay slot.
        regs = {"pc": (self.read_register("o7") + 8) & 0xFFFFFFFF}
        if ret_value is not None:
            regs["o0"] = ret_value & 0xFFFFFFFF
        self.write_registers(regs)
        self.cont()


# Map halucinator arch strings → the mixin class that provides calling
# conventions. QEMUBackend/UnicornBackend/others look this up to pick
# the right ABI at instantiation time.

class M68KHalMixin(_ABIBase):
    """
    Motorola 68000 / ColdFire ABI (System V m68k): all arguments are passed on
    the STACK, the return address is the longword at [sp] after a ``jsr``, and
    the return value comes back in ``d0``.

    Stack at function entry (jsr has already pushed the return address):
        [sp] = return addr, [sp+4] = arg0, [sp+8] = arg1, ...
    """
    WORD_SIZE = 4
    REGISTERS = (
        tuple(f"d{i}" for i in range(8))
        + tuple(f"a{i}" for i in range(8))
        + ("pc", "sr")
    )

    def get_arg(self, idx: int) -> int:
        if idx < 0:
            raise ValueError(f"Argument index must be non-negative, got {idx}")
        sp = self.read_register("sp")
        return self.read_memory(sp + (idx + 1) * 4, 4, 1)

    def set_args(self, args: List[int]) -> None:
        # Write the args above the return address without moving sp; the
        # caller owns stack cleanup, as on x86 cdecl.
        sp = self.read_register("sp")
        for i, v in enumerate(args):
            self.write_memory(sp + (i + 1) * 4, 4, v)

    def get_ret_addr(self) -> int:
        return self.read_memory(self.read_register("sp"), 4, 1)

    def set_ret_addr(self, ret_addr: int) -> None:
        self.write_memory(self.read_register("sp"), 4, ret_addr)

    def execute_return(self, ret_value: int) -> None:
        # Emulate `rts`: pop the return address and jump to it.
        sp = self.read_register("sp")
        ret_addr = self.read_memory(sp, 4, 1)
        regs = {"sp": sp + 4, "pc": ret_addr}
        if ret_value is not None:
            regs["d0"] = ret_value & 0xFFFFFFFF
        self.write_registers(regs)
        self.cont()

ABI_MIXINS: Dict[str, type] = {
    "cortex-m3": ARM32HalMixin,
    "arm":       ARM32HalMixin,
    "arm64":     ARM64HalMixin,
    "mips":      MIPSHalMixin,
    # Little-endian MIPS32 shares the o32 calling convention with big-endian
    # MIPS -- endianness is a data-layout property, not an ABI one -- so the
    # same mixin serves both. Without an entry here _bind_abi falls back to
    # ARM32HalMixin *silently* (the fallback IS the default, so the rebinding
    # branch is skipped), and the first intercept to read an argument dies with
    # "Unknown register: 'r0'" because r0 is not in the MIPS register map.
    "mipsel":    MIPSHalMixin,
    "powerpc":   PowerPCHalMixin,
    "powerpc:MPC8XX": PowerPCHalMixin,
    "ppc64":     PowerPC64HalMixin,
    "x86":       X86HalMixin,
    "riscv32":   RISCVHalMixin,
    "tricore":   TriCoreHalMixin,
    # Without this entry _bind_abi falls back to ARM32HalMixin *silently* --
    # the fallback IS the default, so the rebinding branch never runs -- and
    # the first intercept to read an argument dies with "Unknown register:
    # 'r0'", because r0 is not in the SPARC register map.
    "sparc":     SPARCHalMixin,
    "m68k":      M68KHalMixin,
}
