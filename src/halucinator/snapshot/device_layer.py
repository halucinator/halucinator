# Copyright 2026 Christopher Wright

"""Layer-3 snapshot: external zmq device-process state.

External devices (``external_devices/*``, connected over the peripheral
server's PUB/SUB ipc pipes) may hold state that is part of the machine — a
protocol bridge's session counters, a panel's latched outputs. PUB/SUB has
**no membership**: HALucinator cannot enumerate who is connected, so this
layer is a *cooperative, opt-in* protocol with a collection window:

  save:    HAL publishes  ``Peripheral.SnapshotDevices.save    {snapshot_id}``
           devices reply  ``Peripheral.SnapshotDevices.state   {snapshot_id,
                                                                device_id,
                                                                state}``
           HAL collects replies for ``timeout`` seconds; whoever answered is
           in the snapshot.
  restore: HAL publishes  ``Peripheral.SnapshotDevices.restore {snapshot_id,
                                                                states}``
           devices reply  ``Peripheral.SnapshotDevices.restored {snapshot_id,
                                                                 device_id,
                                                                 ok}``
           every device captured in the snapshot must ack ok within
           ``timeout`` or the restore reports failure (all-or-nothing, per
           the snapshot contract — a missing device means the machine cannot
           be brought back whole).

Stateless devices (a uart terminal that only displays, bridges whose sockets
cannot be checkpointed anyway) simply don't participate; they are absent from
the snapshot and the contract ignores them. Device authors opt in with
:class:`halucinator.external_devices.snapshotable.SnapshotableDevice`.

This layer is opt-in on the HAL side too: pass a :class:`DeviceLayer` to
``system_snapshot``/``system_restore`` to include it. The fast in-memory
checkpoint path should NOT enable it — the collection window (a full
``timeout`` every save, because membership is unknowable) is disk-checkpoint
money, not per-input money.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any, Dict

from ..peripheral_models import peripheral_server
from ..peripheral_models.peripheral_server import (
    encode_zmq_msg,
    peripheral_model,
    reg_rx_handler,
)

log = logging.getLogger(__name__)


@peripheral_model
class SnapshotDevices:
    """Rx-side collector for device replies, dispatched by the peripheral
    server's existing topic router (``Peripheral.SnapshotDevices.*``).

    Class-level dicts are transient plumbing, NOT machine state — the
    explicit no-op save/restore below keeps the Layer-2 registry from
    capturing them (a snapshot of the snapshot machinery would be turtles
    all the way down).
    """

    _lock = threading.Lock()
    # snapshot_id -> {device_id: state}
    collected: Dict[str, Dict[str, Any]] = {}
    # snapshot_id -> {device_id: ok(bool)}
    acked: Dict[str, Dict[str, bool]] = {}

    @classmethod
    @reg_rx_handler
    def state(cls, msg: Dict[str, Any]) -> None:
        snap_id = msg.get("snapshot_id")
        device_id = msg.get("device_id")
        if snap_id is None or device_id is None:
            log.error("SnapshotDevices.state: malformed reply %r", msg)
            return
        with cls._lock:
            if snap_id not in cls.collected:
                log.warning("SnapshotDevices.state: reply for unknown/expired "
                            "snapshot %s from %s; dropped", snap_id, device_id)
                return
            cls.collected[snap_id][device_id] = msg.get("state")

    @classmethod
    @reg_rx_handler
    def restored(cls, msg: Dict[str, Any]) -> None:
        snap_id = msg.get("snapshot_id")
        device_id = msg.get("device_id")
        if snap_id is None or device_id is None:
            log.error("SnapshotDevices.restored: malformed ack %r", msg)
            return
        with cls._lock:
            if snap_id not in cls.acked:
                log.warning("SnapshotDevices.restored: ack for unknown/expired "
                            "restore %s from %s; dropped", snap_id, device_id)
                return
            cls.acked[snap_id][device_id] = bool(msg.get("ok"))

    # -- Layer-2 registry: nothing here is machine state ------------------
    @classmethod
    def save_state(cls) -> Dict[str, Any]:
        return {}

    @classmethod
    def restore_state(cls, _state: Dict[str, Any]) -> bool:
        return True


def _publish(topic: str, data: Dict[str, Any]) -> bool:
    """Publish on the peripheral server's TX socket. False if the server
    isn't running (no socket)."""
    sock = peripheral_server.__TX_SOCKET__
    if sock is None:
        return False
    sock.send_string(encode_zmq_msg(topic, data))
    return True


class DeviceLayer:
    """Save/restore external device state over the peripheral server.

    ``timeout`` is the collection window (seconds). It is paid in full on
    every ``snapshot()`` — PUB/SUB anonymity means there is no "everyone has
    answered" signal, only "the window closed".
    """

    SAVE_TOPIC = "Peripheral.SnapshotDevices.save"
    RESTORE_TOPIC = "Peripheral.SnapshotDevices.restore"

    def __init__(self, timeout: float = 1.0, poll_interval: float = 0.02):
        self.timeout = timeout
        self.poll_interval = poll_interval

    def available(self) -> bool:
        """True when the peripheral server is up (there is a TX socket)."""
        return peripheral_server.__TX_SOCKET__ is not None

    def snapshot(self) -> Dict[str, Any]:
        """Collect state from every participating device. Returns
        ``{device_id: state}`` — empty when no server is running or nothing
        answered (both legitimate: a fleet of stateless devices)."""
        snap_id = uuid.uuid4().hex
        with SnapshotDevices._lock:
            SnapshotDevices.collected[snap_id] = {}
        try:
            if not _publish(self.SAVE_TOPIC, {"snapshot_id": snap_id}):
                log.debug("DeviceLayer.snapshot: peripheral server not "
                          "running; empty device layer")
                return {}
            deadline = time.time() + self.timeout
            while time.time() < deadline:
                time.sleep(self.poll_interval)
            with SnapshotDevices._lock:
                states = dict(SnapshotDevices.collected[snap_id])
        finally:
            with SnapshotDevices._lock:
                SnapshotDevices.collected.pop(snap_id, None)
        log.info("DeviceLayer.snapshot: captured %d device(s)%s",
                 len(states),
                 (": " + ", ".join(sorted(states))) if states else "")
        return states

    def restore(self, states: Dict[str, Any]) -> bool:
        """Push captured state back to the devices. All-or-nothing: every
        device in *states* must ack ok within the window, else False. An
        empty *states* is trivially True (nothing was captured)."""
        if not states:
            return True
        snap_id = uuid.uuid4().hex
        with SnapshotDevices._lock:
            SnapshotDevices.acked[snap_id] = {}
        try:
            if not _publish(self.RESTORE_TOPIC,
                            {"snapshot_id": snap_id, "states": states}):
                log.error("DeviceLayer.restore: %d captured device(s) but "
                          "the peripheral server is not running",
                          len(states))
                return False
            deadline = time.time() + self.timeout
            while time.time() < deadline:
                with SnapshotDevices._lock:
                    acks = dict(SnapshotDevices.acked[snap_id])
                if (set(acks) >= set(states)
                        and all(acks[d] for d in states)):
                    return True  # everyone in, early exit — acks have membership
                if any(d in acks and not acks[d] for d in states):
                    break  # an explicit NACK can't improve; fail now
                time.sleep(self.poll_interval)
            with SnapshotDevices._lock:
                acks = dict(SnapshotDevices.acked[snap_id])
        finally:
            with SnapshotDevices._lock:
                SnapshotDevices.acked.pop(snap_id, None)
        missing = sorted(set(states) - set(acks))
        nacked = sorted(d for d in states if d in acks and not acks[d])
        log.error("DeviceLayer.restore failed: missing ack from %s; "
                  "nack from %s", missing or "-", nacked or "-")
        return False
