"""
HALucinator backend factory.

Usage:
    from halucinator.backends import get_backend
    backend = get_backend(config)  # returns a HalBackend instance
"""
from .hal_backend import HalBackend, MemoryRegion, ARMHalMixin

__all__ = ["HalBackend", "MemoryRegion", "ARMHalMixin", "get_backend"]


def get_backend(config=None, backend_type: str = "avatar2", **kwargs):
    """
    Factory: return a concrete HalBackend for *backend_type*.

    backend_type:
        "avatar2"  — wraps existing avatar2 + QEMU (default, zero breakage)
        "qemu"     — direct GDB+QMP, no avatar2
        "unicorn"  — in-process unicorn-engine (ARM/ARM64/MIPS/PPC)
        "renode"   — Antmicro Renode via GDB stub + Monitor TCP
        "ghidra"   — in-process Ghidra PCode EmulatorHelper via pyghidra
    """
    if backend_type == "avatar2":
        from .avatar2_backend import Avatar2Backend
        return Avatar2Backend(config=config, **kwargs)
    elif backend_type == "qemu":
        from .qemu_backend import QEMUBackend
        return QEMUBackend(config=config, **kwargs)
    elif backend_type == "unicorn":
        from .unicorn_backend import UnicornBackend
        return UnicornBackend(config=config, **kwargs)
    elif backend_type == "renode":
        from .renode_backend import RenodeBackend
        return RenodeBackend(config=config, **kwargs)
    elif backend_type == "ghidra":
        from .ghidra_backend import GhidraBackend
        return GhidraBackend(config=config, **kwargs)
    else:
        raise ValueError(
            f"Unknown backend type: {backend_type!r}. "
            f"Valid: 'avatar2', 'qemu', 'unicorn', 'renode', 'ghidra'"
        )
