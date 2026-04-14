"""
Tests for halucinator.peripheral_models.utty (UTTYInterface and UTTYModel)
"""
from collections import deque
from unittest import mock

import pytest

from halucinator.peripheral_models.utty import UTTYInterface, UTTYModel
from halucinator.peripheral_models.interrupts import Interrupts


@pytest.fixture(autouse=True)
def clean_state():
    """Reset UTTYModel class state between tests."""
    UTTYModel.interfaces = {}
    UTTYModel.unattached_interfaces = {}
    Interrupts.active.clear()
    Interrupts.enabled.clear()
    Interrupts.Active_Interrupts.clear()
    yield


# ===================== UTTYInterface tests =====================

class TestUTTYInterface:

    def test_init_defaults(self):
        iface = UTTYInterface("uart0")
        assert iface.interface_id == "uart0"
        assert iface.enabled is True
        assert iface.irq_num is None
        assert iface.irq_enabled is True
        assert len(iface.rx_queue) == 0
        assert len(iface.tx_queue) == 0

    def test_init_custom(self):
        iface = UTTYInterface(1, enabled=False, irq_num=42)
        assert iface.interface_id == 1
        assert iface.enabled is False
        assert iface.irq_num == 42

    def test_enable_disable(self):
        iface = UTTYInterface("uart0", enabled=False)
        assert iface.enabled is False
        iface.enable()
        assert iface.enabled is True
        iface.disable()
        assert iface.enabled is False

    def test_flush(self):
        iface = UTTYInterface("uart0")
        iface.rx_queue.extend([1, 2, 3])
        iface.flush()
        assert len(iface.rx_queue) == 0

    def test_disable_irq(self):
        iface = UTTYInterface("uart0")
        assert iface.irq_enabled is True
        iface.disable_irq()
        assert iface.irq_enabled is False

    @mock.patch.object(Interrupts, "clear_active_bp")
    def test_clear_irq(self, mock_clear):
        iface = UTTYInterface("uart0", irq_num=10)
        iface.clear_irq()
        mock_clear.assert_called_once_with(10)

    @mock.patch.object(Interrupts, "clear_active_bp")
    def test_enable_irq_bp(self, mock_clear):
        iface = UTTYInterface("uart0", irq_num=10)
        iface.enable_irq_bp()
        mock_clear.assert_called_once_with(10)

    @mock.patch.object(Interrupts, "set_active_bp")
    def test_fire_interrupt_bp_with_data_and_irq(self, mock_set):
        iface = UTTYInterface("uart0", irq_num=5)
        iface.rx_queue.append(0x41)
        iface._fire_interrupt_bp()
        mock_set.assert_called_once_with(5)

    @mock.patch.object(Interrupts, "set_active_bp")
    def test_fire_interrupt_bp_no_data(self, mock_set):
        iface = UTTYInterface("uart0", irq_num=5)
        iface._fire_interrupt_bp()
        mock_set.assert_not_called()

    @mock.patch.object(Interrupts, "set_active_bp")
    def test_fire_interrupt_bp_no_irq(self, mock_set):
        iface = UTTYInterface("uart0", irq_num=None)
        iface.rx_queue.append(0x41)
        iface._fire_interrupt_bp()
        mock_set.assert_not_called()

    @mock.patch.object(Interrupts, "set_active_qmp")
    def test_fire_interrupt_qmp_with_data_and_irq(self, mock_set):
        iface = UTTYInterface("uart0", irq_num=7)
        iface.rx_queue.append(0x41)
        iface._fire_interrupt_qmp()
        mock_set.assert_called_once_with(7)

    @mock.patch.object(Interrupts, "set_active_qmp")
    def test_fire_interrupt_qmp_no_data(self, mock_set):
        iface = UTTYInterface("uart0", irq_num=7)
        iface._fire_interrupt_qmp()
        mock_set.assert_not_called()

    @mock.patch.object(Interrupts, "set_active_qmp")
    def test_fire_interrupt_qmp_no_irq(self, mock_set):
        iface = UTTYInterface("uart0", irq_num=None)
        iface.rx_queue.append(0x41)
        iface._fire_interrupt_qmp()
        mock_set.assert_not_called()

    @mock.patch.object(Interrupts, "set_active_qmp")
    def test_buffer_rx_chars_qmp_enabled(self, mock_set):
        iface = UTTYInterface("uart0", irq_num=3)
        iface.buffer_rx_chars_qmp([0x41, 0x42, 0x43])
        assert list(iface.rx_queue) == [0x41, 0x42, 0x43]
        mock_set.assert_called_once_with(3)

    @mock.patch.object(Interrupts, "set_active_qmp")
    def test_buffer_rx_chars_qmp_disabled(self, mock_set):
        iface = UTTYInterface("uart0", enabled=False, irq_num=3)
        iface.buffer_rx_chars_qmp([0x41])
        assert len(iface.rx_queue) == 0
        mock_set.assert_not_called()

    @mock.patch.object(Interrupts, "set_active_qmp")
    def test_buffer_rx_chars_qmp_bytes(self, mock_set):
        iface = UTTYInterface("uart0", irq_num=3)
        iface.buffer_rx_chars_qmp(b"\x41\x42")
        assert list(iface.rx_queue) == [0x41, 0x42]

    @mock.patch.object(Interrupts, "clear_active_bp")
    def test_get_rx_char_with_data(self, mock_clear):
        iface = UTTYInterface("uart0", irq_num=5)
        iface.rx_queue.extend([0x41, 0x42])
        char = iface.get_rx_char()
        assert char == 0x41
        # Queue still has data, no clear_irq
        mock_clear.assert_not_called()

    @mock.patch.object(Interrupts, "clear_active_bp")
    def test_get_rx_char_last_item_clears_irq(self, mock_clear):
        iface = UTTYInterface("uart0", irq_num=5)
        iface.rx_queue.append(0x41)
        char = iface.get_rx_char()
        assert char == 0x41
        mock_clear.assert_called_once_with(5)

    def test_get_rx_char_empty(self):
        iface = UTTYInterface("uart0")
        assert iface.get_rx_char() == 0x00

    def test_get_rx_buff_size(self):
        iface = UTTYInterface("uart0")
        assert iface.get_rx_buff_size() == 0
        iface.rx_queue.extend([1, 2, 3])
        assert iface.get_rx_buff_size() == 3

    @mock.patch.object(Interrupts, "set_active_qmp")
    def test_buffer_tx_char_qmp_enabled(self, mock_set):
        iface = UTTYInterface("uart0", irq_num=3)
        iface.buffer_tx_char_qmp(0x41)
        assert list(iface.tx_queue) == [0x41]
        # _fire_interrupt_qmp only fires if rx_queue is non-empty
        mock_set.assert_not_called()

    @mock.patch.object(Interrupts, "set_active_qmp")
    def test_buffer_tx_char_qmp_fires_irq_when_rx_has_data(self, mock_set):
        iface = UTTYInterface("uart0", irq_num=3)
        iface.rx_queue.append(0x42)  # rx_queue non-empty needed for irq
        iface.buffer_tx_char_qmp(0x41)
        assert list(iface.tx_queue) == [0x41]
        mock_set.assert_called_once_with(3)

    @mock.patch.object(Interrupts, "set_active_qmp")
    def test_buffer_tx_char_qmp_disabled(self, mock_set):
        iface = UTTYInterface("uart0", enabled=False, irq_num=3)
        iface.buffer_tx_char_qmp(0x41)
        assert len(iface.tx_queue) == 0

    def test_get_tx_char_with_data(self):
        iface = UTTYInterface("uart0")
        iface.tx_queue.extend([0x41, 0x42])
        assert iface.get_tx_char() == 0x41
        assert iface.get_tx_char() == 0x42

    def test_get_tx_char_empty(self):
        iface = UTTYInterface("uart0")
        assert iface.get_tx_char() is None

    def test_get_tx_buff_size(self):
        iface = UTTYInterface("uart0")
        assert iface.get_tx_buff_size() == 0
        iface.tx_queue.extend([1, 2])
        assert iface.get_tx_buff_size() == 2


# ===================== UTTYModel tests =====================

class TestUTTYModel:

    def test_add_interface(self):
        UTTYModel.add_interface("uart0", enabled=True, irq_num=10)
        assert "uart0" in UTTYModel.unattached_interfaces
        assert "uart0" not in UTTYModel.interfaces

    def test_attach_interface(self):
        UTTYModel.add_interface("uart0", enabled=True, irq_num=10)
        result = UTTYModel.attach_interface("uart0")
        assert result is True
        assert "uart0" in UTTYModel.interfaces
        assert "uart0" not in UTTYModel.unattached_interfaces

    def test_attach_interface_not_found(self):
        result = UTTYModel.attach_interface("nonexistent")
        assert result is False

    def test_enable(self):
        UTTYModel.add_interface("uart0", enabled=False)
        UTTYModel.attach_interface("uart0")
        UTTYModel.enable("uart0")
        assert UTTYModel.interfaces["uart0"].enabled is True

    def test_disable(self):
        UTTYModel.add_interface("uart0", enabled=True)
        UTTYModel.attach_interface("uart0")
        UTTYModel.disable("uart0")
        assert UTTYModel.interfaces["uart0"].enabled is False

    def test_flush(self):
        UTTYModel.add_interface("uart0")
        UTTYModel.attach_interface("uart0")
        UTTYModel.interfaces["uart0"].rx_queue.extend([1, 2, 3])
        UTTYModel.flush("uart0")
        assert len(UTTYModel.interfaces["uart0"].rx_queue) == 0

    @mock.patch("halucinator.peripheral_models.peripheral_server.__TX_SOCKET__")
    def test_tx_buf(self, mock_socket):
        mock_socket.send_string = mock.Mock()
        UTTYModel.tx_buf("uart0", b"hello")
        mock_socket.send_string.assert_called_once()
        call_arg = mock_socket.send_string.call_args[0][0]
        assert "UTTYModel.tx_buf" in call_arg

    @mock.patch.object(Interrupts, "set_active_qmp")
    def test_rx_char_or_buf_int(self, mock_set):
        UTTYModel.add_interface("uart0", irq_num=5)
        UTTYModel.attach_interface("uart0")
        msg = {"interface_id": "uart0", "char": 65}
        UTTYModel.rx_char_or_buf(msg)
        assert 65 in UTTYModel.interfaces["uart0"].rx_queue

    @mock.patch.object(Interrupts, "set_active_qmp")
    def test_rx_char_or_buf_buffer(self, mock_set):
        UTTYModel.add_interface("uart0", irq_num=5)
        UTTYModel.attach_interface("uart0")
        msg = {"interface_id": "uart0", "char": [65, 66, 67]}
        UTTYModel.rx_char_or_buf(msg)
        assert list(UTTYModel.interfaces["uart0"].rx_queue) == [65, 66, 67]

    def test_rx_char_or_buf_no_interface(self):
        """Should not raise when interface not attached."""
        msg = {"interface_id": "nonexistent", "char": 65}
        UTTYModel.rx_char_or_buf(msg)  # should log and not raise

    @mock.patch.object(Interrupts, "clear_active_bp")
    def test_get_rx_char(self, mock_clear):
        UTTYModel.add_interface("uart0", irq_num=5)
        UTTYModel.attach_interface("uart0")
        UTTYModel.interfaces["uart0"].rx_queue.append("A")
        char = UTTYModel.get_rx_char("uart0")
        assert char == ord("A")

    def test_get_rx_char_int_passthrough(self):
        UTTYModel.add_interface("uart0")
        UTTYModel.attach_interface("uart0")
        UTTYModel.interfaces["uart0"].rx_queue.append(65)
        char = UTTYModel.get_rx_char("uart0")
        assert char == 65

    def test_get_rx_char_empty(self):
        UTTYModel.add_interface("uart0")
        UTTYModel.attach_interface("uart0")
        char = UTTYModel.get_rx_char("uart0")
        assert char == 0x00

    def test_get_rx_buff_size(self):
        UTTYModel.add_interface("uart0")
        UTTYModel.attach_interface("uart0")
        assert UTTYModel.get_rx_buff_size("uart0") == 0
        UTTYModel.interfaces["uart0"].rx_queue.extend([1, 2, 3])
        assert UTTYModel.get_rx_buff_size("uart0") == 3
