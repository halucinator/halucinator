"""
Archs specifies the halucinator specific configuration needed to support various
target architectures.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Iterator, Optional

from avatar2 import ARM_CORTEX_M3, ARM, ARM64, PPC32, PPC64, PPC_MPC8544DS
from avatar2.archs.mips import MIPS_BE
import halucinator


_QEMU_DEFAULT_LOC = os.path.join(
    os.path.split(os.path.split(halucinator.__path__[0])[0])[0], "deps/build-qemu"
)


def _get_halucinator_targets() -> Dict[str, Dict[str, Any]]:
    """Lazily import qemu_targets to avoid circular import."""
    from halucinator.qemu_targets import (
        ARMQemuTarget,
        ARMv7mQemuTarget,
        ARM64QemuTarget,
        MIPSQemuTarget,
        PowerPCQemuTarget,
        PowerPC64QemuTarget,
    )

    return {
        "cortex-m3": {
            "avatar_arch": ARM_CORTEX_M3,
            "qemu_target": ARMv7mQemuTarget,
            "qemu_env_var": "HALUCINATOR_QEMU_ARM",
            "qemu_default_path": os.path.join(
                _QEMU_DEFAULT_LOC, "arm-softmmu/qemu-system-arm"
            ),
        },
        "arm": {
            "avatar_arch": ARM,
            "qemu_target": ARMQemuTarget,
            "qemu_env_var": "HALUCINATOR_QEMU_ARM",
            "qemu_default_path": os.path.join(
                _QEMU_DEFAULT_LOC, "arm-softmmu/qemu-system-arm"
            ),
        },
        "arm64": {
            "avatar_arch": ARM64,
            "qemu_target": ARM64QemuTarget,
            "qemu_env_var": "HALUCINATOR_QEMU_ARM64",
            "qemu_default_path": os.path.join(
                _QEMU_DEFAULT_LOC, "aarch64-softmmu/qemu-system-aarch64"
            ),
        },
        "mips": {
            "avatar_arch": MIPS_BE,
            "qemu_target": MIPSQemuTarget,
            "qemu_env_var": "HALUCINATOR_QEMU_MIPS",
            "qemu_default_path": os.path.join(
                _QEMU_DEFAULT_LOC, "mips-softmmu/qemu-system-mips"
            ),
        },
        "powerpc": {
            "avatar_arch": PPC32,
            "qemu_target": PowerPCQemuTarget,
            "qemu_env_var": "HALUCINATOR_QEMU_PPC",
            "qemu_default_path": os.path.join(
                _QEMU_DEFAULT_LOC, "ppc-softmmu/qemu-system-ppc"
            ),
        },
        "powerpc:MPC8XX": {
            "avatar_arch": PPC_MPC8544DS,
            "qemu_target": PowerPCQemuTarget,
            "qemu_env_var": "HALUCINATOR_QEMU_PPC",
            "qemu_default_path": os.path.join(
                _QEMU_DEFAULT_LOC, "ppc-softmmu/qemu-system-ppc"
            ),
        },
        "ppc64": {
            "avatar_arch": PPC64,
            "qemu_target": PowerPC64QemuTarget,
            "qemu_env_var": "HALUCINATOR_QEMU_PPC64",
            "qemu_default_path": os.path.join(
                _QEMU_DEFAULT_LOC, "ppc64-softmmu/qemu-system-ppc64"
            ),
        },
    }


# Lazy proxy: populated on first access
class _LazyTargets(dict):  # type: ignore[type-arg]
    _loaded: bool = False

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self._loaded = True
            self.update(_get_halucinator_targets())

    def __getitem__(self, key: str) -> Any:
        self._ensure_loaded()
        return super().__getitem__(key)

    def __contains__(self, key: object) -> bool:
        self._ensure_loaded()
        return super().__contains__(key)

    def __iter__(self) -> Iterator[str]:
        self._ensure_loaded()
        return super().__iter__()

    def keys(self) -> Any:
        self._ensure_loaded()
        return super().keys()

    def values(self) -> Any:
        self._ensure_loaded()
        return super().values()

    def items(self) -> Any:
        self._ensure_loaded()
        return super().items()

    def get(self, key: str, default: Optional[Any] = None) -> Any:
        self._ensure_loaded()
        return super().get(key, default)


HALUCINATOR_TARGETS = _LazyTargets()
