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
        # Monotonic id of the current in-QEMU syx snapshot. QEMU keeps exactly
        # one; taking a new snapshot supersedes the previous, so a Snapshot
        # handle carries the generation it was minted at and restore refuses a
        # superseded one.
        self._syx_generation = 0

    # ------------------------------------------------------------------
    # Snapshot / restore — fast in-QEMU checkpoint via libafl syx-snapshot
    #
    # The generic QEMUBackend fallback reads all of guest RAM back over the
    # GDB stub (slow). The libafl-qemu-bridge instead exposes syx-snapshot
    # (a whole-machine RAM+device checkpoint) through the custom QMP commands
    # `libafl-syx-snapshot` / `libafl-syx-restore`, so save/restore happen
    # entirely inside QEMU — the fast path an iterative loop needs.
    # ------------------------------------------------------------------

    def can_snapshot(self) -> bool:
        return True

    def snapshot_is_fast(self) -> bool:
        return True

    def save_state(self, portable: bool = False) -> "Snapshot":
        from .hal_backend import Snapshot, SnapshotError
        if portable:
            # A syx snapshot lives inside the QEMU process and cannot be
            # serialized to disk. For the portable/persistent form, fall back
            # to the generic reg+RAM capture (which IS picklable).
            return super().save_state(portable=True)
        resp = self._qmp.execute("libafl-syx-snapshot")
        if resp.get("error"):
            raise SnapshotError(
                f"libafl-syx-snapshot QMP command failed: {resp['error']}")
        self._syx_generation += 1
        return Snapshot(backend_type=self.__class__.__name__,
                        version=self.SNAPSHOT_VERSION,
                        data={"syx": True, "generation": self._syx_generation})

    def restore_state(self, snap: "Snapshot") -> bool:
        from .hal_backend import log_snapshot_mismatch
        data = snap.data if isinstance(snap.data, dict) else {}
        if not data.get("syx"):
            # A generic (portable) snapshot — restore via the base reg+RAM
            # path, not the syx QMP command.
            return super().restore_state(snap)
        if snap.backend_type != self.__class__.__name__:
            log_snapshot_mismatch(self, snap, "backend_type")
            return False
        if snap.version != self.SNAPSHOT_VERSION:
            log_snapshot_mismatch(self, snap, "version")
            return False
        if data.get("generation") != self._syx_generation:
            log.error("libafl-syx-restore: snapshot (generation %s) was "
                      "superseded by a newer syx snapshot (%s); QEMU keeps "
                      "only the latest", data.get("generation"),
                      self._syx_generation)
            return False
        resp = self._qmp.execute("libafl-syx-restore")
        if resp.get("error"):
            log.error("libafl-syx-restore QMP command failed: %r",
                      resp["error"])
            return False
        return True

    # ------------------------------------------------------------------
    # Edge coverage — native libafl-qemu AFL-style edge instrumentation
    #
    # libafl-qemu compiles the edge-coverage TCG hooks and a 64 KB AFL
    # hitcount map into the binary, but nothing registers the hook unless
    # the embedded Rust harness does. The bridge's `libafl-cov-open` /
    # `libafl-cov-result` QMP commands register it from C and fold the map
    # diff host-side, so a coverage-guided loop over GDB+QMP gets native
    # instrumentation speed without shipping the 64 KB map each iteration.
    #
    # The coverage map is host-side (not guest RAM / vmstate), so a syx
    # restore does not touch it — the caller resets it via coverage_open().
    # ------------------------------------------------------------------

    def coverage_available(self) -> bool:
        return True

    def coverage_open(self) -> bool:
        """Register the edge-coverage hook and zero the coverage maps.

        Idempotent enable (the hook is registered once, which tb_flushes so
        already-translated blocks re-translate with instrumentation) plus a
        reset of the current and cumulative maps. Call once up front. Returns False if the command errored.
        """
        resp = self._qmp.execute("libafl-cov-open")
        if resp.get("error"):
            log.error("libafl-cov-open QMP command failed: %r", resp["error"])
            return False
        return True

    def coverage_result(self) -> Optional[Dict[str, int]]:
        """Fold the last run's edges into the cumulative set and reset the
        current map for the next run.

        Returns ``{"new_edges": N, "total_edges": M}`` where *new_edges* is how
        many edges the last run covered that no prior run had, and *total_edges*
        is the cumulative distinct-edge count. Returns None on error.
        """
        resp = self._qmp.execute("libafl-cov-result")
        if resp.get("error"):
            log.error("libafl-cov-result QMP command failed: %r", resp["error"])
            return None
        ret = resp.get("return", {})
        return {"new_edges": int(ret.get("new-edges", 0)),
                "total_edges": int(ret.get("total-edges", 0))}
