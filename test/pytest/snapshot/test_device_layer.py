"""Layer-3 (external zmq device) snapshot tests.

Driven through fakes — a stand-in TX socket on the HAL side and a recording
IOServer on the device side — so the protocol logic is tested deterministically
without real zmq plumbing or its timing flakes.
"""
import pytest

from halucinator.peripheral_models import peripheral_server
from halucinator.peripheral_models.peripheral_server import decode_zmq_msg
from halucinator.snapshot import DeviceLayer, SystemSnapshot, system_restore
from halucinator.snapshot.device_layer import SnapshotDevices
from halucinator.external_devices.snapshotable import SnapshotableDevice

from .test_backend_snapshot import DictBackend


class FakeTxSocket:
    """Captures publishes; optionally reacts like a connected device."""

    def __init__(self):
        self.sent = []          # decoded (topic, msg) tuples
        self.on_send = None     # optional callback(topic, msg)

    def send_string(self, s):
        topic, msg = decode_zmq_msg(s)
        self.sent.append((topic, msg))
        if self.on_send is not None:
            self.on_send(topic, msg)


@pytest.fixture
def fake_server(monkeypatch):
    sock = FakeTxSocket()
    monkeypatch.setattr(peripheral_server, "__TX_SOCKET__", sock)
    return sock


@pytest.fixture
def no_server(monkeypatch):
    monkeypatch.setattr(peripheral_server, "__TX_SOCKET__", None)


def fast_layer():
    return DeviceLayer(timeout=0.15, poll_interval=0.01)


class TestDeviceLayerSnapshot:
    def test_no_server_yields_empty_layer(self, no_server):
        layer = fast_layer()
        assert layer.available() is False
        assert layer.snapshot() == {}

    def test_collects_replies_within_window(self, fake_server):
        def reply(topic, msg):
            if topic == DeviceLayer.SAVE_TOPIC:
                SnapshotDevices.state({"snapshot_id": msg["snapshot_id"],
                                       "device_id": "bridge-1",
                                       "state": {"count": 7}})
                SnapshotDevices.state({"snapshot_id": msg["snapshot_id"],
                                       "device_id": "panel-2",
                                       "state": {"leds": [1, 0]}})
        fake_server.on_send = reply

        states = fast_layer().snapshot()
        assert states == {"bridge-1": {"count": 7}, "panel-2": {"leds": [1, 0]}}
        # collection slot is cleaned up afterwards
        assert SnapshotDevices.collected == {}

    def test_silent_fleet_yields_empty(self, fake_server):
        assert fast_layer().snapshot() == {}
        assert fake_server.sent[0][0] == DeviceLayer.SAVE_TOPIC

    def test_stale_reply_is_dropped(self, fake_server):
        # A reply for a snapshot that is no longer collecting must not crash
        # or resurrect the slot.
        SnapshotDevices.state({"snapshot_id": "expired", "device_id": "d",
                               "state": {}})
        assert "expired" not in SnapshotDevices.collected

    def test_malformed_reply_is_ignored(self, fake_server):
        SnapshotDevices.state({"device_id": "no-snapshot-id"})
        SnapshotDevices.state({"snapshot_id": "no-device-id"})
        assert SnapshotDevices.collected == {}


class TestDeviceLayerRestore:
    def test_empty_states_trivially_true_without_publishing(self, fake_server):
        assert fast_layer().restore({}) is True
        assert fake_server.sent == []

    def test_no_server_with_captured_devices_fails(self, no_server):
        assert fast_layer().restore({"bridge-1": {}}) is False

    def test_all_acked_early_exit(self, fake_server):
        def ack(topic, msg):
            if topic == DeviceLayer.RESTORE_TOPIC:
                for dev in msg["states"]:
                    SnapshotDevices.restored({"snapshot_id": msg["snapshot_id"],
                                              "device_id": dev, "ok": True})
        fake_server.on_send = ack
        # A 30 s window that must return promptly: pass ⇒ the early exit
        # (full-membership ack check) worked, not the timeout.
        layer = DeviceLayer(timeout=30.0, poll_interval=0.01)
        assert layer.restore({"bridge-1": {"count": 7}}) is True
        assert SnapshotDevices.acked == {}

    def test_missing_ack_fails_after_window(self, fake_server):
        def ack_one(topic, msg):
            if topic == DeviceLayer.RESTORE_TOPIC:
                SnapshotDevices.restored({"snapshot_id": msg["snapshot_id"],
                                          "device_id": "bridge-1", "ok": True})
        fake_server.on_send = ack_one
        assert fast_layer().restore({"bridge-1": {}, "gone-2": {}}) is False

    def test_explicit_nack_fails(self, fake_server):
        def nack(topic, msg):
            if topic == DeviceLayer.RESTORE_TOPIC:
                SnapshotDevices.restored({"snapshot_id": msg["snapshot_id"],
                                          "device_id": "bridge-1", "ok": False})
        fake_server.on_send = nack
        assert fast_layer().restore({"bridge-1": {}}) is False

    def test_malformed_ack_is_ignored(self, fake_server):
        # Missing snapshot_id or device_id: dropped, no crash, no state.
        SnapshotDevices.restored({"device_id": "d", "ok": True})
        SnapshotDevices.restored({"snapshot_id": "s", "ok": True})
        assert SnapshotDevices.acked == {}

    def test_stale_ack_is_dropped(self, fake_server):
        # An ack for a restore that is no longer awaiting must not resurrect
        # the slot.
        SnapshotDevices.restored({"snapshot_id": "expired", "device_id": "d",
                                  "ok": True})
        assert "expired" not in SnapshotDevices.acked


class TestCoordinatorIntegration:
    def test_captured_devices_but_no_layer_fails_restore(self):
        b = DictBackend()
        snap = SystemSnapshot(backend=b.save_state(), peripherals={},
                              devices={"bridge-1": {"count": 7}})
        # No peripherals were captured, so an empty-registry restore is fine;
        # the device layer is what must refuse.
        res = system_restore(b, snap)
        assert res.ok is False
        assert res.layer == "devices"

    def test_devices_restored_through_coordinator(self, fake_server):
        def ack(topic, msg):
            if topic == DeviceLayer.RESTORE_TOPIC:
                for dev in msg["states"]:
                    SnapshotDevices.restored({"snapshot_id": msg["snapshot_id"],
                                              "device_id": dev, "ok": True})
        fake_server.on_send = ack
        b = DictBackend()
        snap = SystemSnapshot(backend=b.save_state(), peripherals={},
                              devices={"bridge-1": {"count": 7}})
        res = system_restore(b, snap, device_layer=fast_layer())
        assert res.ok is True
        assert "devices" in res.message

    def test_pre_layer3_snapshot_restores_fine(self):
        """A SystemSnapshot pickled before `devices` existed has no such
        attribute — it must restore as an empty device layer, not crash."""
        b = DictBackend()
        snap = SystemSnapshot(backend=b.save_state(), peripherals={})
        del snap.devices  # simulate the old pickle layout
        res = system_restore(b, snap)
        assert res.ok is True


class FakeIOServer:
    def __init__(self):
        self.topics = {}
        self.sent = []

    def register_topic(self, topic, method):
        self.topics[topic] = method

    def send_msg(self, topic, data):
        self.sent.append((topic, data))


class TestIOServerResilience:
    """IOServer.run() must not let one bad message/handler kill the rx loop
    (the second containment layer behind finding 1). Driven with fakes — no
    real zmq — so it's deterministic."""

    def _drive(self, messages, handlers):
        """Run IOServer.run()'s loop over a fixed message list via fakes."""
        import threading
        from halucinator.external_devices.ioserver import IOServer
        from halucinator.peripheral_models.peripheral_server import (
            encode_zmq_msg,
        )

        io = IOServer.__new__(IOServer)  # skip __init__ (no real sockets)
        io._IOServer__stop = threading.Event()
        io.handlers = handlers
        io.packet_log = None
        encoded = iter([encode_zmq_msg(t, d) for t, d in messages])

        class FakeRx:
            def recv_string(self_inner):
                return next(encoded)
        io.rx_socket = FakeRx()

        def poll(_timeout):
            # Yield POLLIN until messages run out, then signal stop.
            try:
                io.rx_socket._next = encoded.__length_hint__()
            except Exception:
                pass
            if encoded.__length_hint__() == 0:
                io._IOServer__stop.set()
                return {}
            return {io.rx_socket: 1}  # zmq.POLLIN == 1
        io.poller = type("P", (), {"poll": staticmethod(poll)})()
        io.run()
        return io

    def test_run_survives_handler_exception(self):
        calls = []

        def boom(_io, _data):
            calls.append("boom")
            raise RuntimeError("handler blew up")

        def ok(_io, _data):
            calls.append("ok")

        self._drive(
            [("T.boom", {"k": 1}), ("T.ok", {"k": 2})],
            {"T.boom": boom, "T.ok": ok},
        )
        # The exception in `boom` did not abort the loop: `ok` still ran.
        assert calls == ["boom", "ok"]

    def test_run_survives_unknown_topic(self):
        calls = []
        self._drive(
            [("T.unknown", {"k": 1}), ("T.ok", {"k": 2})],
            {"T.ok": lambda _i, _d: calls.append("ok")},
        )
        assert calls == ["ok"]  # unknown topic skipped, loop continued


class TestSnapshotableDevice:
    def _make(self, get_state=None, set_state=None):
        io = FakeIOServer()
        holder = {"state": {"count": 1}}
        dev = SnapshotableDevice(
            io, "dev-1",
            get_state or (lambda: dict(holder["state"])),
            set_state or holder["state"].update)
        return io, dev, holder

    def test_registers_both_topics(self):
        io, dev, _ = self._make()
        assert set(io.topics) == {SnapshotableDevice.SAVE_TOPIC,
                                  SnapshotableDevice.RESTORE_TOPIC}

    def test_save_replies_with_state(self):
        io, dev, _ = self._make()
        io.topics[SnapshotableDevice.SAVE_TOPIC](io, {"snapshot_id": "s1"})
        assert io.sent == [(SnapshotableDevice.STATE_TOPIC,
                            {"snapshot_id": "s1", "device_id": "dev-1",
                             "state": {"count": 1}})]

    def test_get_state_failure_stays_silent(self):
        def boom():
            raise RuntimeError("no state for you")
        io, dev, _ = self._make(get_state=boom)
        io.topics[SnapshotableDevice.SAVE_TOPIC](io, {"snapshot_id": "s1"})
        assert io.sent == []  # absent from the snapshot, not lying in it

    def test_restore_applies_state_and_acks(self):
        io, dev, holder = self._make()
        io.topics[SnapshotableDevice.RESTORE_TOPIC](
            io, {"snapshot_id": "s2",
                 "states": {"dev-1": {"count": 42}}})
        assert holder["state"] == {"count": 42}
        assert io.sent == [(SnapshotableDevice.RESTORED_TOPIC,
                            {"snapshot_id": "s2", "device_id": "dev-1",
                             "ok": True})]

    def test_restore_not_involving_us_is_ignored(self):
        io, dev, holder = self._make()
        io.topics[SnapshotableDevice.RESTORE_TOPIC](
            io, {"snapshot_id": "s3", "states": {"other-dev": {}}})
        assert io.sent == []
        assert holder["state"] == {"count": 1}

    def test_set_state_failure_nacks(self):
        def boom(_state):
            raise RuntimeError("cannot apply")
        io, dev, _ = self._make(set_state=boom)
        io.topics[SnapshotableDevice.RESTORE_TOPIC](
            io, {"snapshot_id": "s4", "states": {"dev-1": {"count": 9}}})
        assert io.sent == [(SnapshotableDevice.RESTORED_TOPIC,
                            {"snapshot_id": "s4", "device_id": "dev-1",
                             "ok": False})]

    def test_malformed_save_is_ignored(self):
        # No snapshot_id: nothing captured, get_state not even called.
        called = []
        io, dev, _ = self._make(get_state=lambda: called.append(1) or {})
        io.topics[SnapshotableDevice.SAVE_TOPIC](io, {"no": "snapshot_id"})
        assert io.sent == []
        assert called == []

    def test_malformed_restore_is_ignored(self):
        # No snapshot_id: set_state not called, nothing acked.
        io, dev, holder = self._make()
        io.topics[SnapshotableDevice.RESTORE_TOPIC](
            io, {"states": {"dev-1": {"count": 5}}})
        assert io.sent == []
        assert holder["state"] == {"count": 1}

    def test_two_devices_on_one_ioserver_both_answer(self):
        """Two devices sharing an IOServer must BOTH answer save (the
        dispatcher fans out) — not silently clobber via register_topic."""
        io = FakeIOServer()
        SnapshotableDevice(io, "dev-a", lambda: {"a": 1}, lambda s: None)
        SnapshotableDevice(io, "dev-b", lambda: {"b": 2}, lambda s: None)
        io.topics[SnapshotableDevice.SAVE_TOPIC](io, {"snapshot_id": "s"})
        replied = {d["device_id"]: d["state"] for _t, d in io.sent}
        assert replied == {"dev-a": {"a": 1}, "dev-b": {"b": 2}}

    def test_duplicate_device_id_raises(self):
        io = FakeIOServer()
        SnapshotableDevice(io, "dup", lambda: {}, lambda s: None)
        with pytest.raises(ValueError, match="duplicate snapshot device_id"):
            SnapshotableDevice(io, "dup", lambda: {}, lambda s: None)

    def test_send_failure_does_not_escape(self):
        """A non-serializable state (send raises) must be contained so the
        IOServer rx thread survives — the device just stays absent."""
        class BoomOnSend(FakeIOServer):
            def send_msg(self, topic, data):
                raise TypeError("cannot represent this state in yaml")

        io = BoomOnSend()
        dev = SnapshotableDevice(io, "dev-1", lambda: {"x": object()},
                                 lambda s: None)
        # Must NOT raise out of the handler (which would kill the rx thread).
        io.topics[SnapshotableDevice.SAVE_TOPIC](io, {"snapshot_id": "s1"})
        io.topics[SnapshotableDevice.RESTORE_TOPIC](
            io, {"snapshot_id": "s2", "states": {"dev-1": {}}})
