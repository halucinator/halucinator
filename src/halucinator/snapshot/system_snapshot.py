# Copyright 2026 Christopher Wright

"""Composite system snapshot: bundles Layer-1 (backend guest CPU+RAM, +native
device state where the emulator has it), Layer-2 (Python peripheral-model
state), and optionally Layer-3 (external zmq device processes) into a single
checkpoint, and restores them together.

Layer 3 is opt-in per call (pass a ``DeviceLayer``): its collection window
costs a full timeout on every save, which is disk-checkpoint money, not
per-restore money — see snapshot/device_layer.py.

Failure contract (per the design):
  * ``system_snapshot`` raises (via the layers' own ``SnapshotError`` /
    RuntimeError) rather than returning a half-captured bundle.
  * ``system_restore`` restores backend, THEN peripherals, THEN devices; on
    the first layer that returns False it stops and reports which layer
    failed, so the caller never resumes a half-restored machine.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from ..backends.hal_backend import HalBackend, Snapshot
from .peripheral_registry import PeripheralRegistry

log = logging.getLogger(__name__)


@dataclass
class RestoreResult:
    """Outcome of :func:`system_restore`. ``ok`` is the headline; on failure
    ``layer`` names the layer that refused ("backend"/"peripherals") and
    ``message`` explains. The machine is inconsistent when ``ok`` is False —
    the caller must re-init rather than resume."""

    ok: bool
    layer: Optional[str] = None
    message: str = ""


@dataclass
class SystemSnapshot:
    """A whole-system checkpoint. Only constructed once every layer captured
    successfully, so possessing one means it is complete. ``devices`` is
    empty unless a DeviceLayer participated (and may legitimately be empty
    with one — a fleet of stateless devices)."""

    backend: Snapshot
    peripherals: Any  # opaque dict from PeripheralRegistry.snapshot()
    devices: Dict[str, Any] = field(default_factory=dict)
    _released: bool = False

    def release(self) -> None:
        """Free resources held by every layer. Idempotent."""
        if self._released:
            return
        try:
            self.backend.release()
        finally:
            self._released = True

    def __enter__(self) -> "SystemSnapshot":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.release()


def system_snapshot(backend: HalBackend,
                    registry: Optional[PeripheralRegistry] = None,
                    portable: bool = False,
                    device_layer: Optional[Any] = None) -> SystemSnapshot:
    """Capture backend + peripheral (+ optionally device) state as one bundle.

    ``portable=True`` requests a picklable, cross-process-safe backend
    capture — required when the bundle is destined for disk
    (:mod:`.persist`). The peripheral layer is plain python either way.

    ``device_layer`` (a :class:`.device_layer.DeviceLayer`) opts Layer 3 in:
    external zmq devices are polled for their state inside the capture.

    Raises ``SnapshotError`` (backend) or ``RuntimeError`` (peripherals) on any
    failure — a partial capture is discarded, never returned. Take this with the
    guest stopped so the layers are consistent.
    """
    if registry is None:
        registry = PeripheralRegistry()
    backend_snap = backend.save_state(portable=portable)  # raises SnapshotError on failure
    try:
        periph_snap = registry.snapshot()        # raises on failure
        devices = device_layer.snapshot() if device_layer is not None else {}
    except Exception:
        backend_snap.release()                   # don't leak the half-bundle
        raise
    return SystemSnapshot(backend=backend_snap, peripherals=periph_snap,
                          devices=devices)


def system_restore(backend: HalBackend, snap: SystemSnapshot,
                   registry: Optional[PeripheralRegistry] = None,
                   device_layer: Optional[Any] = None) -> RestoreResult:
    """Restore a :class:`SystemSnapshot`: backend, then peripherals, then
    (when captured) external devices.

    Stops at the first layer that returns False and reports it. No layer is
    restored past a failure, so the caller gets a clear "inconsistent, re-init"
    signal instead of a silently half-restored machine.
    """
    if registry is None:
        registry = PeripheralRegistry()

    if not backend.restore_state(snap.backend):
        return RestoreResult(
            ok=False, layer="backend",
            message="backend.restore_state returned False "
                    "(incompatible snapshot or restore error)")

    if not registry.restore(snap.peripherals):
        return RestoreResult(
            ok=False, layer="peripherals",
            message="peripheral registry restore returned False "
                    "(a captured target is missing or failed)")

    # `devices` is absent on snapshots pickled before Layer 3 existed —
    # treat those as empty (nothing was captured, nothing to push back).
    devices = getattr(snap, "devices", {}) or {}
    if devices:
        if device_layer is None:
            return RestoreResult(
                ok=False, layer="devices",
                message=f"snapshot captured {len(devices)} external "
                        "device(s) but no DeviceLayer was passed to "
                        "system_restore")
        if not device_layer.restore(devices):
            return RestoreResult(
                ok=False, layer="devices",
                message="device layer restore failed (a captured device "
                        "is missing, silent, or nacked)")

    return RestoreResult(ok=True,
                         message="restored backend + peripherals"
                                 + (" + devices" if devices else ""))
