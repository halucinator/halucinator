# Copyright 2026 Christopher Wright

"""Device-side opt-in for Layer-3 snapshots (see snapshot/device_layer.py).

An external device process that holds machine state participates by wiring a
``SnapshotableDevice`` onto its :class:`IOServer`:

    from halucinator.external_devices.ioserver import IOServer
    from halucinator.external_devices.snapshotable import SnapshotableDevice

    class MyBridge:
        def get_state(self):        # -> yaml-safe dict
            return {"sessions": self.sessions, "counter": self.counter}
        def set_state(self, state): # inverse, restore in place
            self.sessions = state["sessions"]
            self.counter = state["counter"]

    ioserver = IOServer(...)
    bridge = MyBridge()
    SnapshotableDevice(ioserver, "my-bridge", bridge.get_state, bridge.set_state)
    ioserver.start()

``device_id`` must be unique per fleet and STABLE across process restarts —
it is the key the restore contract checks, so a renamed device makes old
snapshots unrestorable (by design: that's a missing device).

State must be yaml-safe (it travels ``encode_zmq_msg`` and is pickled into
snapshot files). ``set_state`` runs on the IOServer rx thread; synchronize
with the device's own threads as needed. A ``set_state`` that raises acks
``ok: false`` so the HAL side fails the restore loudly instead of resuming
against a half-restored fleet.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict

log = logging.getLogger(__name__)


SAVE_TOPIC = "Peripheral.SnapshotDevices.save"
RESTORE_TOPIC = "Peripheral.SnapshotDevices.restore"
STATE_TOPIC = "Peripheral.SnapshotDevices.state"
RESTORED_TOPIC = "Peripheral.SnapshotDevices.restored"


class _SnapshotDispatcher:
    """One per IOServer. ``register_topic`` stores a single handler per topic,
    so N SnapshotableDevices on one IOServer would clobber each other's
    save/restore handlers — all but the last silently never answering. The
    dispatcher owns the two topic registrations once and fans each message
    out to every device, keyed by a unique device_id."""

    def __init__(self, ioserver: Any) -> None:
        self.ioserver = ioserver
        self.devices: Dict[str, "SnapshotableDevice"] = {}
        ioserver.register_topic(SAVE_TOPIC, self._on_save)
        ioserver.register_topic(RESTORE_TOPIC, self._on_restore)

    @classmethod
    def for_ioserver(cls, ioserver: Any) -> "_SnapshotDispatcher":
        disp = getattr(ioserver, "_snapshot_dispatcher", None)
        if disp is None:
            disp = cls(ioserver)
            ioserver._snapshot_dispatcher = disp
        return disp

    def add(self, device: "SnapshotableDevice") -> None:
        if device.device_id in self.devices:
            raise ValueError(
                f"duplicate snapshot device_id {device.device_id!r} on this "
                "IOServer; device_id must be unique per fleet")
        self.devices[device.device_id] = device

    def _on_save(self, _ioserver: Any, msg: Dict[str, Any]) -> None:
        for dev in list(self.devices.values()):
            dev._handle_save(msg)

    def _on_restore(self, _ioserver: Any, msg: Dict[str, Any]) -> None:
        for dev in list(self.devices.values()):
            dev._handle_restore(msg)


class SnapshotableDevice:
    """Answers the HAL side's Layer-3 save/restore protocol for one device."""

    # Re-exported as class attrs for callers/tests that referenced them here.
    SAVE_TOPIC = SAVE_TOPIC
    RESTORE_TOPIC = RESTORE_TOPIC
    STATE_TOPIC = STATE_TOPIC
    RESTORED_TOPIC = RESTORED_TOPIC

    def __init__(self, ioserver: Any, device_id: str,
                 get_state: Callable[[], Dict[str, Any]],
                 set_state: Callable[[Dict[str, Any]], Any]) -> None:
        self.ioserver = ioserver
        self.device_id = device_id
        self.get_state = get_state
        self.set_state = set_state
        # Register via the shared per-IOServer dispatcher, not directly, so a
        # second device on the same IOServer doesn't clobber the first.
        _SnapshotDispatcher.for_ioserver(ioserver).add(self)

    def _send(self, topic: str, payload: Dict[str, Any]) -> bool:
        """Send a protocol reply, containing any serialization/transport
        failure. A raw exception here would escape the IOServer rx loop and
        kill the whole device thread (it answers ALL topics), so a single
        bad-state device must never take itself fully offline."""
        try:
            self.ioserver.send_msg(topic, payload)
            return True
        except Exception:  # noqa: BLE001
            log.exception("SnapshotableDevice[%s]: sending %s failed (state "
                          "not yaml-safe?); staying absent rather than "
                          "killing the device", self.device_id, topic)
            return False

    def _handle_save(self, msg: Dict[str, Any]) -> None:
        snap_id = msg.get("snapshot_id")
        if snap_id is None:
            log.error("SnapshotableDevice[%s]: malformed save %r",
                      self.device_id, msg)
            return
        try:
            state = self.get_state()
        except Exception:  # noqa: BLE001
            log.exception("SnapshotableDevice[%s]: get_state failed; "
                          "not answering (device will be absent from the "
                          "snapshot)", self.device_id)
            return
        self._send(STATE_TOPIC,
                   {"snapshot_id": snap_id, "device_id": self.device_id,
                    "state": state})

    def _handle_restore(self, msg: Dict[str, Any]) -> None:
        snap_id = msg.get("snapshot_id")
        states = msg.get("states") or {}
        if snap_id is None:
            log.error("SnapshotableDevice[%s]: malformed restore %r",
                      self.device_id, msg)
            return
        if self.device_id not in states:
            return  # this restore doesn't involve us
        ok = True
        try:
            self.set_state(states[self.device_id])
        except Exception:  # noqa: BLE001
            log.exception("SnapshotableDevice[%s]: set_state failed",
                          self.device_id)
            ok = False
        self._send(RESTORED_TOPIC,
                   {"snapshot_id": snap_id, "device_id": self.device_id,
                    "ok": ok})
