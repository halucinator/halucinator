"""
Tests for halucinator.peripheral_models.ethernet (EthernetInterface and EthernetModel)

Covers the interface-level methods and model class methods not exercised
by the existing integration tests (test_ethernet_model.py, etc.).
"""
import time
from collections import defaultdict, deque
from unittest import mock

import pytest

from halucinator.peripheral_models.ethernet import EthernetInterface, EthernetModel
from halucinator.peripheral_models.interrupts import Interrupts


@pytest.fixture(autouse=True)
def clean_state():
    """Reset all class-level state between tests."""
    EthernetModel.frame_queues = defaultdict(deque)
    EthernetModel.frame_times = defaultdict(deque)
    EthernetModel.interfaces = dict()
    EthernetModel.calc_crc = True
    EthernetModel.rx_frame_isr = None
    EthernetModel.rx_isr_enabled = False
    Interrupts.active.clear()
    Interrupts.enabled.clear()
    Interrupts.Active_Interrupts.clear()
    yield


# ===================== EthernetInterface tests =====================

class TestEthernetInterface:

    def test_init_defaults(self):
        iface = EthernetInterface("eth0")
        assert iface.interface_id == "eth0"
        assert iface.enabled is True
        assert iface.calc_crc is True
        assert iface.irq_num is None
        assert len(iface.rx_queue) == 0
        assert len(iface.frame_times) == 0

    def test_init_custom(self):
        iface = EthernetInterface(1, enabled=False, calc_crc=False, irq_num=42)
        assert iface.interface_id == 1
        assert iface.enabled is False
        assert iface.calc_crc is False
        assert iface.irq_num == 42

    def test_enable_disable(self):
        iface = EthernetInterface("eth0", enabled=False)
        iface.enable()
        assert iface.enabled is True
        iface.disable()
        assert iface.enabled is False

    def test_flush(self):
        iface = EthernetInterface("eth0")
        iface.rx_queue.append(b"frame1")
        iface.flush()
        assert len(iface.rx_queue) == 0

    def test_disable_irq(self):
        iface = EthernetInterface("eth0")
        iface.disable_irq()
        assert iface.irq_enabled is False

    @mock.patch.object(Interrupts, "clear_active_bp")
    def test_enable_irq_bp(self, mock_clear):
        iface = EthernetInterface("eth0", irq_num=10)
        iface.enable_irq_bp()
        mock_clear.assert_called_once_with(10)

    @mock.patch.object(Interrupts, "set_active_bp")
    def test_fire_interrupt_bp_with_data_and_irq(self, mock_set):
        iface = EthernetInterface("eth0", irq_num=5)
        iface.rx_queue.append(b"frame")
        iface._fire_interrupt_bp()
        mock_set.assert_called_once_with(5)

    @mock.patch.object(Interrupts, "set_active_bp")
    def test_fire_interrupt_bp_empty_queue(self, mock_set):
        iface = EthernetInterface("eth0", irq_num=5)
        iface._fire_interrupt_bp()
        mock_set.assert_not_called()

    @mock.patch.object(Interrupts, "set_active_bp")
    def test_fire_interrupt_bp_no_irq(self, mock_set):
        iface = EthernetInterface("eth0", irq_num=None)
        iface.rx_queue.append(b"frame")
        iface._fire_interrupt_bp()
        mock_set.assert_not_called()

    @mock.patch.object(Interrupts, "set_active_qmp")
    def test_fire_interrupt_qmp(self, mock_set):
        iface = EthernetInterface("eth0", irq_num=7)
        iface.rx_queue.append(b"frame")
        iface._fire_interrupt_qmp()
        mock_set.assert_called_once_with(7)

    @mock.patch.object(Interrupts, "set_active_qmp")
    def test_fire_interrupt_qmp_no_data(self, mock_set):
        iface = EthernetInterface("eth0", irq_num=7)
        iface._fire_interrupt_qmp()
        mock_set.assert_not_called()

    @mock.patch.object(Interrupts, "set_active_qmp")
    def test_buffer_frame_qmp_enabled(self, mock_set):
        iface = EthernetInterface("eth0", irq_num=3)
        frame = b"\x00\x01\x02"
        iface.buffer_frame_qmp(frame)
        assert list(iface.rx_queue) == [frame]
        assert len(iface.frame_times) == 1
        mock_set.assert_called_once_with(3)

    @mock.patch.object(Interrupts, "set_active_qmp")
    def test_buffer_frame_qmp_disabled(self, mock_set):
        iface = EthernetInterface("eth0", enabled=False, irq_num=3)
        iface.buffer_frame_qmp(b"frame")
        assert len(iface.rx_queue) == 0
        mock_set.assert_not_called()

    def test_get_frame_no_time(self):
        iface = EthernetInterface("eth0")
        iface.rx_queue.append(b"frame1")
        iface.frame_times.append(1234.0)
        result = iface.get_frame(get_time=False)
        assert result == b"frame1"

    def test_get_frame_with_time(self):
        iface = EthernetInterface("eth0")
        iface.rx_queue.append(b"frame1")
        iface.frame_times.append(1234.0)
        frame, rx_time = iface.get_frame(get_time=True)
        assert frame == b"frame1"
        assert rx_time == 1234.0

    def test_get_frame_empty(self):
        iface = EthernetInterface("eth0")
        result = iface.get_frame(get_time=False)
        assert result is None

    def test_get_frame_empty_with_time(self):
        iface = EthernetInterface("eth0")
        frame, rx_time = iface.get_frame(get_time=True)
        assert frame is None
        assert rx_time is None

    def test_get_frame_info_with_data(self):
        iface = EthernetInterface("eth0")
        iface.rx_queue.append(b"abcd")
        iface.rx_queue.append(b"ef")
        num_frames, first_len = iface.get_frame_info()
        assert num_frames == 2
        assert first_len == 4

    def test_get_frame_info_empty(self):
        iface = EthernetInterface("eth0")
        assert iface.get_frame_info() == (0, 0)


# ===================== EthernetModel tests =====================

class TestEthernetModel:

    def test_add_interface(self):
        EthernetModel.add_interface("eth0", enabled=True, calc_crc=False, irq_num=10)
        assert "eth0" in EthernetModel.interfaces
        iface = EthernetModel.interfaces["eth0"]
        assert iface.calc_crc is False
        assert iface.irq_num == 10

    def test_enable_disable_interface(self):
        EthernetModel.add_interface("eth0")
        EthernetModel.disable("eth0")
        assert EthernetModel.interfaces["eth0"].enabled is False
        EthernetModel.enable("eth0")
        assert EthernetModel.interfaces["eth0"].enabled is True

    def test_flush_interface(self):
        EthernetModel.add_interface("eth0")
        EthernetModel.interfaces["eth0"].rx_queue.append(b"frame")
        EthernetModel.flush("eth0")
        assert len(EthernetModel.interfaces["eth0"].rx_queue) == 0

    @mock.patch.object(Interrupts, "clear_active_bp")
    def test_enable_rx_isr_bp(self, mock_clear):
        EthernetModel.add_interface("eth0", irq_num=5)
        EthernetModel.enable_rx_isr_bp("eth0")
        mock_clear.assert_called_once_with(5)

    def test_enable_rx_isr_bp_nonexistent(self):
        # Should not raise for missing interface
        EthernetModel.enable_rx_isr_bp("nonexistent")

    def test_disable_rx_isr_bp(self):
        EthernetModel.add_interface("eth0")
        EthernetModel.disable_rx_isr_bp("eth0")
        assert EthernetModel.interfaces["eth0"].irq_enabled is False

    def test_disable_rx_isr_bp_nonexistent(self):
        # Should not raise
        EthernetModel.disable_rx_isr_bp("nonexistent")

    def test_enable_rx_isr(self):
        EthernetModel.rx_isr_enabled = False
        EthernetModel.enable_rx_isr("eth0")
        assert EthernetModel.rx_isr_enabled is True

    def test_disable_rx_isr(self):
        EthernetModel.rx_isr_enabled = True
        EthernetModel.disable_rx_isr("eth0")
        assert EthernetModel.rx_isr_enabled is False

    @mock.patch("halucinator.peripheral_models.peripheral_server.__TX_SOCKET__")
    def test_tx_frame(self, mock_socket):
        mock_socket.send_string = mock.Mock()
        EthernetModel.tx_frame("eth0", b"\x00\x01\x02")
        mock_socket.send_string.assert_called_once()
        call_arg = mock_socket.send_string.call_args[0][0]
        assert "EthernetModel.tx_frame" in call_arg

    def test_rx_frame(self):
        msg = {"interface_id": "eth0", "frame": b"\x00\x01\x02"}
        EthernetModel.rx_frame(msg)
        assert len(EthernetModel.frame_queues["eth0"]) == 1
        assert EthernetModel.frame_queues["eth0"][0] == b"\x00\x01\x02"
        assert len(EthernetModel.frame_times["eth0"]) == 1

    @mock.patch("halucinator.peripheral_models.peripheral_server.irq_set_qmp")
    def test_rx_frame_triggers_isr_when_enabled(self, mock_irq):
        EthernetModel.rx_frame_isr = 20
        EthernetModel.rx_isr_enabled = True
        Interrupts.enabled[20] = True
        Interrupts.active[20] = True
        msg = {"interface_id": "eth0", "frame": b"frame"}
        EthernetModel.rx_frame(msg)
        mock_irq.assert_called()

    def test_rx_frame_no_isr_when_disabled(self):
        EthernetModel.rx_frame_isr = 20
        EthernetModel.rx_isr_enabled = False
        msg = {"interface_id": "eth0", "frame": b"frame"}
        with mock.patch.object(Interrupts, "trigger_interrupt") as mock_trigger:
            EthernetModel.rx_frame(msg)
            mock_trigger.assert_not_called()

    def test_get_rx_frame_no_time(self):
        EthernetModel.frame_queues["eth0"].append(b"frame1")
        EthernetModel.frame_times["eth0"].append(100.0)
        result = EthernetModel.get_rx_frame("eth0", get_time=False)
        assert result == b"frame1"

    def test_get_rx_frame_with_time(self):
        EthernetModel.frame_queues["eth0"].append(b"frame1")
        EthernetModel.frame_times["eth0"].append(100.0)
        frame, rx_time = EthernetModel.get_rx_frame("eth0", get_time=True)
        assert frame == b"frame1"
        assert rx_time == 100.0

    def test_get_rx_frame_empty(self):
        assert EthernetModel.get_rx_frame("eth0") is None

    def test_get_rx_frame_empty_with_time(self):
        frame, rx_time = EthernetModel.get_rx_frame("eth0", get_time=True)
        assert frame is None
        assert rx_time is None

    def test_get_rx_frame_only(self):
        EthernetModel.frame_queues["eth0"].append(b"frame")
        EthernetModel.frame_times["eth0"].append(100.0)
        result = EthernetModel.get_rx_frame_only("eth0")
        assert result == b"frame"

    def test_get_rx_frame_only_empty(self):
        assert EthernetModel.get_rx_frame_only("eth0") is None

    def test_get_rx_frame_and_time(self):
        EthernetModel.frame_queues["eth0"].append(b"frame")
        EthernetModel.frame_times["eth0"].append(200.0)
        frame, rx_time = EthernetModel.get_rx_frame_and_time("eth0")
        assert frame == b"frame"
        assert rx_time == 200.0

    def test_get_rx_frame_and_time_empty(self):
        frame, rx_time = EthernetModel.get_rx_frame_and_time("eth0")
        assert frame is None
        assert rx_time is None

    def test_get_frame_info_with_data(self):
        EthernetModel.frame_queues["eth0"].append(b"abcd")
        EthernetModel.frame_queues["eth0"].append(b"ef")
        num, first_len = EthernetModel.get_frame_info("eth0")
        assert num == 2
        assert first_len == 4

    def test_get_frame_info_empty(self):
        assert EthernetModel.get_frame_info("eth0") == (0, 0)

    @mock.patch("halucinator.peripheral_models.peripheral_server.irq_set_qmp")
    def test_enable_rx_isr_triggers_when_frames_queued(self, mock_irq):
        EthernetModel.rx_frame_isr = 15
        EthernetModel.frame_queues["eth0"].append(b"frame")
        Interrupts.enabled[15] = True
        Interrupts.active[15] = True
        EthernetModel.enable_rx_isr("eth0")
        assert EthernetModel.rx_isr_enabled is True
        mock_irq.assert_called()

    def test_enable_rx_isr_no_trigger_when_no_isr(self):
        EthernetModel.rx_frame_isr = None
        EthernetModel.frame_queues["eth0"].append(b"frame")
        with mock.patch.object(Interrupts, "trigger_interrupt") as mock_trigger:
            EthernetModel.enable_rx_isr("eth0")
            mock_trigger.assert_not_called()
