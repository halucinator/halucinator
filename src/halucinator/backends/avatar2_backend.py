"""
Avatar2Backend — wraps the existing avatar2 + QEMU stack behind HalBackend.

This is a thin delegation layer: every HalBackend method is forwarded to the
underlying avatar2 QemuTarget instance.  The goal is zero-breakage migration:
code that currently holds a QemuTarget can be given an Avatar2Backend instead
and will behave identically.

Architecture-specific subclasses (ARM, AArch64, …) are created by wrapping
the corresponding qemu_targets classes defined in halucinator.qemu_targets.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from .hal_backend import HalBackend, MemoryRegion


class Avatar2Backend(HalBackend):
    """
    HalBackend implementation that delegates to an avatar2 QemuTarget.

    The *target* attribute holds the underlying QemuTarget; callers that need
    avatar2-specific features (e.g. QMP monitor) can access it directly.
    """

    def __init__(self, target: Any = None, config: Any = None, **kwargs: Any):
        """
        Parameters
        ----------
        target:
            An already-instantiated avatar2 QemuTarget.  When None the backend
            is uninitialised; call ``attach(target)`` before use.
        config:
            Optional HalucinatorConfig — stored for use by the factory.
        """
        self.target = target
        self.config = config

    def attach(self, target: Any) -> None:
        """Attach this backend to an existing avatar2 QemuTarget."""
        self.target = target

    # ------------------------------------------------------------------
    # Memory
    # ------------------------------------------------------------------

    def read_memory(self, addr: int, size: int, num_words: int = 1,
                    raw: bool = False) -> Union[int, bytes]:
        return self.target.read_memory(addr, size, num_words, raw=raw)

    def write_memory(self, addr: int, size: int,
                     value: Union[int, bytes, bytearray],
                     num_words: int = 1, raw: bool = False) -> bool:
        return self.target.write_memory(addr, size, value, num_words, raw=raw)

    # ------------------------------------------------------------------
    # Registers
    # ------------------------------------------------------------------

    def read_register(self, register: str) -> int:
        return self.target.read_register(register)

    def write_register(self, register: str, value: int) -> None:
        self.target.write_register(register, value)

    # ------------------------------------------------------------------
    # Execution control
    # ------------------------------------------------------------------

    def set_breakpoint(self, addr: int, hardware: bool = False,
                       temporary: bool = False) -> int:
        return self.target.set_breakpoint(addr, hardware=hardware,
                                          temporary=temporary)

    def remove_breakpoint(self, bp_id: int) -> None:
        self.target.remove_breakpoint(bp_id)

    def set_watchpoint(self, addr: int, write: bool = True,
                       read: bool = False) -> int:
        return self.target.set_watchpoint(addr, write=write, read=read)

    def cont(self, blocking: bool = True) -> None:
        self.target.cont(blocking=blocking)

    def stop(self) -> None:
        self.target.stop()

    def step(self) -> None:
        self.target.step()

    # ------------------------------------------------------------------
    # Memory regions — delegate to avatar2 config
    # ------------------------------------------------------------------

    def add_memory_region(self, region: MemoryRegion) -> None:
        """
        Forward a MemoryRegion to the avatar2 Avatar config.

        This mirrors what halucinator's setup_memory() does today; the Avatar2
        backend is the place where QEMU/avatar2-specific fields (qemu_name,
        qemu_properties) are consumed.
        """
        if self.target is None:
            raise RuntimeError("Avatar2Backend.attach() must be called first")
        self.target.avatar.add_memory_range(
            region.base_addr,
            region.size,
            name=region.name,
            permissions=region.permissions,
            file=region.file,
            emulate=region.emulate,
            qemu_name=region.qemu_name,
            qemu_properties=region.qemu_properties,
        )

    # ------------------------------------------------------------------
    # Optional: inject IRQ via avatar2 QMP monitor
    # ------------------------------------------------------------------

    def inject_irq(self, irq_num: int) -> None:
        self.target.protocols.monitor.execute_command(
            "avatar-armv7m-inject-irq",
            {"num_irq": irq_num, "num_cpu": 0},
        )

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        if self.target is not None:
            try:
                self.target.shutdown()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Pass-through for attributes the rest of halucinator still uses
    # directly (e.g. target.regs, target.avatar, target.protocols, …)
    # ------------------------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        """Transparent proxy: forward unknown attribute access to target."""
        if name in ("target", "config"):
            raise AttributeError(name)
        if self.target is not None:
            return getattr(self.target, name)
        raise AttributeError(
            f"Avatar2Backend.target is None — "
            f"cannot proxy attribute {name!r}"
        )
