"""
LibAflQemuBackend — drive the LibAFL QEMU bridge as a halucinator backend.

The LibAFL bridge (https://github.com/halucinator/libafl-qemu-bridge) is a
patched, newer QEMU (10.0) that carries the avatar-qemu hooks plus a
fuzzing surface (snapshots, edge-coverage callbacks, sync-exits). For
halucinator we treat it as just another QEMU build that exposes the
``configurable`` machine type and the Halucinator-IRQ QOM types — same
GDB-RSP + QMP control protocol, same expected machine config JSON.

This backend is a thin subclass of :class:`QEMUBackend` that only
overrides binary resolution:

* If ``qemu_path`` is passed explicitly, that wins.
* Otherwise the per-arch env vars ``HALUCINATOR_QEMU_LIBAFL_*`` are
  consulted (e.g. ``HALUCINATOR_QEMU_LIBAFL_ARM``,
  ``HALUCINATOR_QEMU_LIBAFL_ARM64``, ``HALUCINATOR_QEMU_LIBAFL_MIPS``,
  ``HALUCINATOR_QEMU_LIBAFL_PPC``, ``HALUCINATOR_QEMU_LIBAFL_PPC64``).
* If neither is set, the backend falls back to the build tree at
  ``deps/build-qemu-libafl/<arch>-softmmu/qemu-system-<arch>`` which
  :mod:`build_qemu.sh` populates when invoked with
  ``--source libafl-qemu-bridge``.

Halucinator's intercept and bp_handler machinery is unaware of which
QEMU variant is on the other side — the bridge's libafl-specific
features (snapshots, edge coverage, sync-exits) are not exposed
through this backend yet; that integration belongs in a dedicated
fuzzing harness on top of HalBackend. See
``src/halucinator/backends/README.md`` for the planned scope.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from .qemu_backend import QEMUBackend


log = logging.getLogger(__name__)


# Per-arch env var lookup, mirroring the table in
# ``halucinator.config.target_archs._get_halucinator_targets`` but pointed
# at the libafl-qemu-bridge build outputs instead of the avatar-qemu ones.
_LIBAFL_ENV_VAR: Dict[str, str] = {
    "cortex-m3":      "HALUCINATOR_QEMU_LIBAFL_ARM",
    "arm":            "HALUCINATOR_QEMU_LIBAFL_ARM",
    "arm64":          "HALUCINATOR_QEMU_LIBAFL_ARM64",
    "mips":           "HALUCINATOR_QEMU_LIBAFL_MIPS",
    "powerpc":        "HALUCINATOR_QEMU_LIBAFL_PPC",
    "powerpc:MPC8XX": "HALUCINATOR_QEMU_LIBAFL_PPC",
    "ppc64":          "HALUCINATOR_QEMU_LIBAFL_PPC64",
}

# Default fallback path inside the repo's standard build-tree layout
# (matches build_qemu.sh --source libafl-qemu-bridge).
_LIBAFL_DEFAULT_SUBDIR: Dict[str, str] = {
    "cortex-m3":      "arm-softmmu/qemu-system-arm",
    "arm":            "arm-softmmu/qemu-system-arm",
    "arm64":          "aarch64-softmmu/qemu-system-aarch64",
    "mips":           "mips-softmmu/qemu-system-mips",
    "powerpc":        "ppc-softmmu/qemu-system-ppc",
    "powerpc:MPC8XX": "ppc-softmmu/qemu-system-ppc",
    "ppc64":          "ppc64-softmmu/qemu-system-ppc64",
}


def _resolve_libafl_qemu_path(arch: str) -> Optional[str]:
    """Return a usable libafl-qemu-bridge binary for *arch*, or None."""
    env_var = _LIBAFL_ENV_VAR.get(arch)
    if env_var is not None:
        path = os.environ.get(env_var)
        if path and os.path.isfile(path):
            return path
    sub = _LIBAFL_DEFAULT_SUBDIR.get(arch)
    if sub is None:
        return None
    # The default location mirrors `_QEMU_DEFAULT_LOC` from target_archs,
    # one directory over.
    import halucinator
    root = os.path.dirname(os.path.dirname(halucinator.__path__[0]))
    candidate = os.path.join(root, "deps", "build-qemu-libafl", sub)
    return candidate if os.path.isfile(candidate) else None


class LibAflQemuBackend(QEMUBackend):
    """
    QEMUBackend variant that runs the LibAFL QEMU bridge binary.

    Identical control surface to :class:`QEMUBackend` — only the
    underlying ``qemu-system-*`` executable changes. Callers select
    this backend via ``halucinator --emulator libafl-qemu`` or by
    instantiating it directly with an explicit ``qemu_path``.
    """

    def __init__(
        self,
        config: Any = None,
        arch: str = "cortex-m3",
        qemu_path: Optional[str] = None,
        **kwargs: Any,
    ):
        if qemu_path is None:
            qemu_path = _resolve_libafl_qemu_path(arch)
            if qemu_path is None:
                env_var = _LIBAFL_ENV_VAR.get(arch, "HALUCINATOR_QEMU_LIBAFL_*")
                log.warning(
                    "LibAflQemuBackend: no libafl-qemu-bridge binary found "
                    "for arch=%r. Set %s, pass qemu_path=, or build via "
                    "`./build_qemu.sh --source libafl-qemu-bridge`.",
                    arch, env_var,
                )
        super().__init__(config=config, arch=arch, qemu_path=qemu_path,
                         **kwargs)
