"""
Archs specifies the halucinator specific configuration needed to support various
target architectures.
"""

import os

import halucinator

from avatar2 import ARM_CORTEX_M3, ARM, ARM64, PPC32, PPC64, PPC_MPC8544DS
from avatar2.archs.mips import MIPS_BE

_QEMU_DEFAULT_LOC = os.path.join(
    os.path.split(os.path.split(halucinator.__path__[0])[0])[0], "deps/build-qemu"
)

# qemu_targets imports are deferred to break the circular import cycle:
#   qemu_targets -> bp_handlers -> hal_config -> target_archs -> qemu_targets
def _qemu_target(name):
    from halucinator import qemu_targets
    return getattr(qemu_targets, name)


def _get_halucinator_targets():
    """Return the raw targets dict. Separated for testability."""
    return {
        "cortex-m3": {
            "avatar_arch": ARM_CORTEX_M3,
            "qemu_target": lambda: _qemu_target("ARMv7mQemuTarget"),
            "qemu_env_var": "HALUCINATOR_QEMU_ARM",
            "qemu_default_path": os.path.join(
                _QEMU_DEFAULT_LOC, "arm-softmmu/qemu-system-arm"
            ),
        },
        "arm": {
            "avatar_arch": ARM,
            "qemu_target": lambda: _qemu_target("ARMQemuTarget"),
            "qemu_env_var": "HALUCINATOR_QEMU_ARM",
            "qemu_default_path": os.path.join(
                _QEMU_DEFAULT_LOC, "arm-softmmu/qemu-system-arm"
            ),
        },
        "arm64": {
            "avatar_arch": ARM64,
            "qemu_target": lambda: _qemu_target("ARM64QemuTarget"),
            "qemu_env_var": "HALUCINATOR_QEMU_ARM64",
            "qemu_default_path": os.path.join(
                _QEMU_DEFAULT_LOC, "aarch64-softmmu/qemu-system-aarch64"
            ),
        },
        "mips": {
            "avatar_arch": MIPS_BE,
            "qemu_target": lambda: _qemu_target("MIPSQemuTarget"),
            "qemu_env_var": "HALUCINATOR_QEMU_MIPS",
            "qemu_default_path": os.path.join(
                _QEMU_DEFAULT_LOC, "mips-softmmu/qemu-system-mips"
            ),
        },
        "powerpc": {
            "avatar_arch": PPC32,
            "qemu_target": lambda: _qemu_target("PowerPCQemuTarget"),
            "qemu_env_var": "HALUCINATOR_QEMU_PPC",
            "qemu_default_path": os.path.join(
                _QEMU_DEFAULT_LOC, "ppc-softmmu/qemu-system-ppc"
            ),
        },
        "powerpc:MPC8XX": {
            "avatar_arch": PPC_MPC8544DS,
            "qemu_target": lambda: _qemu_target("PowerPCQemuTarget"),
            "qemu_env_var": "HALUCINATOR_QEMU_PPC",
            "qemu_default_path": os.path.join(
                _QEMU_DEFAULT_LOC, "ppc-softmmu/qemu-system-ppc"
            ),
        },
        "ppc64": {
            "avatar_arch": PPC64,
            "qemu_target": lambda: _qemu_target("PowerPC64QemuTarget"),
            "qemu_env_var": "HALUCINATOR_QEMU_PPC64",
            "qemu_default_path": os.path.join(
                _QEMU_DEFAULT_LOC, "ppc64-softmmu/qemu-system-ppc64"
            ),
        },
    }


class _LazyTargets:
    """Dict-like wrapper that defers loading qemu_targets until first access."""

    def __init__(self):
        self._data = None
        self._loaded = False

    def _ensure_loaded(self):
        if not self._loaded:
            self._data = _get_halucinator_targets()
            self._loaded = True

    def __getitem__(self, key):
        self._ensure_loaded()
        return self._data[key]

    def __contains__(self, key):
        self._ensure_loaded()
        return key in self._data

    def __iter__(self):
        self._ensure_loaded()
        return iter(self._data)

    def keys(self):
        self._ensure_loaded()
        return self._data.keys()

    def values(self):
        self._ensure_loaded()
        return self._data.values()

    def items(self):
        self._ensure_loaded()
        return self._data.items()

    def get(self, key, default=None):
        self._ensure_loaded()
        return self._data.get(key, default)


## To add a target to HALUCINATOR register it here — backed by _LazyTargets
## so qemu_targets classes are only imported when actually needed.
HALUCINATOR_TARGETS = _LazyTargets()


def get_backend_for_arch(arch: str, emulator: str = "avatar2"):
    """
    Return a (partially-constructed) HalBackend for *arch* using *emulator*.

    emulator:
        "avatar2"  — Avatar2Backend wrapping the arch-specific QemuTarget
        "qemu"     — direct QEMUBackend (arch-agnostic for now)
        "unicorn"  — UnicornBackend
    """
    from halucinator.backends import get_backend
    return get_backend(backend_type=emulator, arch=arch)
