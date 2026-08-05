# Copyright 2026 Christopher Wright

"""Layer-2 snapshot: Python host-side peripheral-model state.

Every backend forwards MMIO to Python peripheral models, so their *class-level*
mutable state (rx buffers, gpio pins, interrupt maps, handler instance dicts,
run stats) is part of the machine state and must be captured/restored alongside
the guest CPU+RAM. This module enumerates those state holders and snapshots them.

Two capture strategies, in priority order:

1. **Explicit** — a model that defines ``save_state()``/``restore_state()``
   (uart, gpio, interrupts) knows exactly which fields are state and restores
   them IN PLACE (so external aliases stay valid). Preferred: nothing is missed.
2. **Generic deep-copy** — for everything else (bp-handler instances, models
   without an explicit impl), :func:`default_save` deep-copies every mutable
   container attribute and :func:`default_restore` writes it back in place.

The ``SnapshotableModel`` mixin exposes strategy 2 as the default so a model can
opt in just by inheriting it.
"""
from __future__ import annotations

import copy
import logging
from collections import deque
from typing import Any, Dict

log = logging.getLogger(__name__)

# Container types we know how to deep-copy AND restore in place (clear+refill),
# which is what preserves aliases like Interrupts.Active_Interrupts is active.
_MUTABLE = (dict, list, set, deque, bytearray)


def _snapshotable_attrs(obj: Any) -> Dict[str, Any]:
    """Return {name: value} for each mutable-container attribute of *obj*
    (a class or instance) — dunder and callable attributes excluded."""
    out: Dict[str, Any] = {}
    for name in dir(obj):
        if name.startswith("__"):
            continue
        try:
            value = getattr(obj, name)
        except Exception:  # noqa: BLE001 -- descriptor may raise; skip it
            continue
        if callable(value):
            continue
        if isinstance(value, _MUTABLE):
            out[name] = value
    return out


def default_save(obj: Any) -> Dict[str, Any]:
    """Deep-copy every mutable container attribute of *obj*. Generic strategy
    for objects without an explicit ``save_state``."""
    return {name: copy.deepcopy(value)
            for name, value in _snapshotable_attrs(obj).items()}


def _restore_in_place(current: Any, saved: Any) -> bool:
    """Overwrite *current* container's contents with *saved* (a deep copy),
    mutating in place so aliases survive. Returns False if the shapes don't
    match a known container pair (caller falls back to setattr).

    The deep copy happens INSIDE the matching branch, so the type-mismatch
    fallback doesn't copy here and then copy again in the caller's setattr."""
    if isinstance(current, dict) and isinstance(saved, dict):
        current.clear()
        current.update(copy.deepcopy(saved))
    elif isinstance(current, (list, deque)) and isinstance(saved, (list, deque)):
        current.clear()
        current.extend(copy.deepcopy(saved))
    elif isinstance(current, set) and isinstance(saved, set):
        current.clear()
        current.update(copy.deepcopy(saved))
    elif isinstance(current, bytearray) and isinstance(saved, (bytes, bytearray)):
        current[:] = saved  # slice-assign copies; bytes/bytearray are flat
    else:
        return False
    return True


def default_restore(obj: Any, state: Dict[str, Any]) -> bool:
    """Inverse of :func:`default_save`. Restores each attribute IN PLACE when
    it is still a matching live container (preserving aliases), else rebinds."""
    for name, saved in state.items():
        current = getattr(obj, name, None)
        if not _restore_in_place(current, saved):
            setattr(obj, name, copy.deepcopy(saved))
    return True


class SnapshotableModel:
    """Mixin giving a peripheral model the generic deep-copy snapshot behavior
    as classmethods. Models that need to exclude fields or restore differently
    override ``save_state``/``restore_state`` directly (uart/gpio/interrupts do)."""

    @classmethod
    def save_state(cls) -> Dict[str, Any]:
        return default_save(cls)

    @classmethod
    def restore_state(cls, state: Dict[str, Any]) -> bool:
        return default_restore(cls, state)


def _save_one(obj: Any) -> Dict[str, Any]:
    """Capture one target using its explicit save_state if present, else generic."""
    save = getattr(obj, "save_state", None)
    if callable(save):
        return save()
    return default_save(obj)


def _restore_one(obj: Any, state: Dict[str, Any]) -> bool:
    """Restore one target using its explicit restore_state if present, else generic."""
    restore = getattr(obj, "restore_state", None)
    if callable(restore):
        return bool(restore(state))
    return default_restore(obj, state)


class PeripheralRegistry:
    """Enumerates the Layer-2 state holders and save/restores them as a unit.

    Sources (all discovered live at snapshot time so nothing is hard-coded):
      * every ``@peripheral_model`` class (``peripheral_server.__PERIPHERAL_MODELS__``)
      * every cached bp-handler instance (``intercepts.initalized_classes``)
      * the global ``hal_stats.stats`` dict

    Targets are keyed by a stable string so restore re-resolves the same live
    object. ``restore`` returns False (without half-applying) if a snapshot
    entry has no matching live target — an inconsistency the caller must handle.
    """

    STATS_KEY = "hal_stats.stats"

    def _models(self) -> Dict[str, Any]:
        from ..peripheral_models import peripheral_server
        out: Dict[str, Any] = {}
        for cls in peripheral_server.__PERIPHERAL_MODELS__:
            out[f"model:{cls.__module__}.{cls.__qualname__}"] = cls
        return out

    def _handlers(self) -> Dict[str, Any]:
        from ..bp_handlers import intercepts
        out: Dict[str, Any] = {}
        for key, inst in intercepts.initalized_classes.items():
            out[f"handler:{key}"] = inst
        return out

    def _targets(self) -> Dict[str, Any]:
        """All live save/restore targets except hal_stats (handled specially)."""
        targets = self._models()
        targets.update(self._handlers())
        return targets

    def snapshot(self) -> Dict[str, Any]:
        """Capture every Layer-2 target. Raises on any failure (never partial):
        a peripheral snapshot is whole or it does not exist."""
        from .. import hal_stats
        try:
            captured = {key: _save_one(obj)
                        for key, obj in self._targets().items()}
            captured[self.STATS_KEY] = copy.deepcopy(hal_stats.stats)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"PeripheralRegistry.snapshot failed: {exc!r}") from exc
        return captured

    def restore(self, state: Dict[str, Any]) -> bool:
        """Restore every captured target. Returns False (before mutating) if a
        captured target is no longer live, so restore is all-or-nothing."""
        from .. import hal_stats
        targets = self._targets()
        for key in state:
            if key == self.STATS_KEY:
                continue
            if key not in targets:
                log.error("PeripheralRegistry.restore: target %s no longer "
                          "present; refusing partial restore", key)
                return False
        for key, saved in state.items():
            if key == self.STATS_KEY:
                hal_stats.stats.clear()
                hal_stats.stats.update(copy.deepcopy(saved))
                continue
            if not _restore_one(targets[key], saved):
                log.error("PeripheralRegistry.restore: target %s failed", key)
                return False
        return True
