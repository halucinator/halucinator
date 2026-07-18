# Copyright 2026 Christopher Wright

"""Snapshot / restore for HALucinator — the foundation for fast state resume.

Three layers, each with ``save_state``/``restore_state``, composed by a
coordinator:

  * Layer 1 — backend guest CPU + RAM (+ native device state): implemented on
    ``HalBackend`` (generic fallback) and specialized per backend
    (``UnicornBackend`` native, fast).
  * Layer 2 — Python peripheral-model host state: :mod:`.peripheral_registry`.
  * Layer 3 — external device processes: Phase 2 (not yet here).

The coordinator (:mod:`.system_snapshot`) bundles the layers into a
``SystemSnapshot`` and restores them together, reporting per-layer failures via
``RestoreResult``.
"""
from __future__ import annotations

from .peripheral_registry import (
    PeripheralRegistry,
    SnapshotableModel,
    default_restore,
    default_save,
)
from .device_layer import DeviceLayer
from .persist import load_snapshot_file, save_snapshot_file
from .system_snapshot import (
    RestoreResult,
    SystemSnapshot,
    system_restore,
    system_snapshot,
)

__all__ = [
    "PeripheralRegistry",
    "SnapshotableModel",
    "default_save",
    "default_restore",
    "RestoreResult",
    "SystemSnapshot",
    "system_snapshot",
    "system_restore",
    "save_snapshot_file",
    "load_snapshot_file",
    "DeviceLayer",
]
