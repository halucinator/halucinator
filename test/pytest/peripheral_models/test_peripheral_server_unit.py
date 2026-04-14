"""Unit tests for halucinator.peripheral_models.peripheral_server module.

Tests the IRQ helper functions and encode/decode, without requiring zmq infrastructure.
"""

from unittest import mock

import pytest

import halucinator.peripheral_models.peripheral_server as ps


class TestEncodeDecodeMsgs:
    def test_encode_zmq_msg(self):
        result = ps.encode_zmq_msg("Peripheral.GPIO.write", {"id": "p0", "value": 1})
        assert result.startswith("Peripheral.GPIO.write ")
        assert "id" in result

    def test_decode_zmq_msg(self):
        encoded = ps.encode_zmq_msg("Peripheral.Test.read", {"key": "val"})
        topic, msg = ps.decode_zmq_msg(encoded)
        assert topic == "Peripheral.Test.read"
        assert msg == {"key": "val"}

    def test_decode_encode_roundtrip(self):
        original_data = {"count": 42, "active": True}
        encoded = ps.encode_zmq_msg("Topic.A", original_data)
        topic, decoded = ps.decode_zmq_msg(encoded)
        assert topic == "Topic.A"
        assert decoded == original_data


def _set_qemu(mock_qemu):
    """Set the module-level __QEMU and return the original."""
    # Access via the mangled name in the module's globals
    original = ps.__dict__.get("_TestIrqFunctions__QEMU", ps.__dict__.get("__QEMU"))
    # Can't access dunder attrs directly; use mock.patch
    return original


class TestIrqFunctions:
    """Test IRQ helper functions when __QEMU is set/not set."""

    def test_irq_set_qmp_with_qemu(self):
        mock_qemu = mock.Mock()
        with mock.patch.object(ps, "_PeripheralServer__QEMU" if hasattr(ps, "_PeripheralServer__QEMU") else "__QEMU", mock_qemu, create=True):
            # Use setattr to work around name mangling
            old = getattr(ps, "__QEMU", None)
            setattr(ps, "__QEMU", mock_qemu)
            try:
                ps.irq_set_qmp(5)
                mock_qemu.irq_set_qmp.assert_called_once_with(5)
            finally:
                setattr(ps, "__QEMU", old)

    def test_irq_set_qmp_without_qemu(self):
        old = getattr(ps, "__QEMU", None)
        setattr(ps, "__QEMU", None)
        try:
            ps.irq_set_qmp(5)  # Should not raise
        finally:
            setattr(ps, "__QEMU", old)

    def test_irq_clear_qmp_with_qemu(self):
        mock_qemu = mock.Mock()
        old = getattr(ps, "__QEMU", None)
        setattr(ps, "__QEMU", mock_qemu)
        try:
            ps.irq_clear_qmp(3)
            mock_qemu.irq_clear_qmp.assert_called_once_with(3)
        finally:
            setattr(ps, "__QEMU", old)

    def test_irq_clear_qmp_without_qemu(self):
        old = getattr(ps, "__QEMU", None)
        setattr(ps, "__QEMU", None)
        try:
            ps.irq_clear_qmp(3)
        finally:
            setattr(ps, "__QEMU", old)

    def test_irq_enable_qmp_with_qemu(self):
        mock_qemu = mock.Mock()
        old = getattr(ps, "__QEMU", None)
        setattr(ps, "__QEMU", mock_qemu)
        try:
            ps.irq_enable_qmp(7)
            mock_qemu.irq_enable_qmp.assert_called_once_with(7)
        finally:
            setattr(ps, "__QEMU", old)

    def test_irq_enable_qmp_without_qemu(self):
        old = getattr(ps, "__QEMU", None)
        setattr(ps, "__QEMU", None)
        try:
            ps.irq_enable_qmp(7)
        finally:
            setattr(ps, "__QEMU", old)

    def test_irq_disable_qmp_with_qemu(self):
        mock_qemu = mock.Mock()
        old = getattr(ps, "__QEMU", None)
        setattr(ps, "__QEMU", mock_qemu)
        try:
            ps.irq_disable_qmp(2)
            mock_qemu.irq_disable_qmp.assert_called_once_with(2)
        finally:
            setattr(ps, "__QEMU", old)

    def test_irq_disable_qmp_without_qemu(self):
        old = getattr(ps, "__QEMU", None)
        setattr(ps, "__QEMU", None)
        try:
            ps.irq_disable_qmp(2)
        finally:
            setattr(ps, "__QEMU", old)

    def test_irq_set_bp_with_qemu(self):
        mock_qemu = mock.Mock()
        old = getattr(ps, "__QEMU", None)
        setattr(ps, "__QEMU", mock_qemu)
        try:
            ps.irq_set_bp(4)
            mock_qemu.irq_set_bp.assert_called_once_with(4)
        finally:
            setattr(ps, "__QEMU", old)

    def test_irq_set_bp_without_qemu(self):
        old = getattr(ps, "__QEMU", None)
        setattr(ps, "__QEMU", None)
        try:
            ps.irq_set_bp(4)
        finally:
            setattr(ps, "__QEMU", old)

    def test_irq_clear_bp_with_qemu(self):
        mock_qemu = mock.Mock()
        old = getattr(ps, "__QEMU", None)
        setattr(ps, "__QEMU", mock_qemu)
        try:
            ps.irq_clear_bp(6)
            mock_qemu.irq_clear_bp.assert_called_once_with(6)
        finally:
            setattr(ps, "__QEMU", old)

    def test_irq_clear_bp_without_qemu(self):
        old = getattr(ps, "__QEMU", None)
        setattr(ps, "__QEMU", None)
        try:
            ps.irq_clear_bp(6)
        finally:
            setattr(ps, "__QEMU", old)

    def test_irq_enable_bp_with_qemu(self):
        mock_qemu = mock.Mock()
        old = getattr(ps, "__QEMU", None)
        setattr(ps, "__QEMU", mock_qemu)
        try:
            ps.irq_enable_bp(8)
            mock_qemu.irq_enable_bp.assert_called_once_with(8)
        finally:
            setattr(ps, "__QEMU", old)

    def test_irq_enable_bp_without_qemu(self):
        old = getattr(ps, "__QEMU", None)
        setattr(ps, "__QEMU", None)
        try:
            ps.irq_enable_bp(8)
        finally:
            setattr(ps, "__QEMU", old)

    def test_irq_disable_bp_with_qemu(self):
        mock_qemu = mock.Mock()
        old = getattr(ps, "__QEMU", None)
        setattr(ps, "__QEMU", mock_qemu)
        try:
            ps.irq_disable_bp(9)
            mock_qemu.irq_disable_bp.assert_called_once_with(9)
        finally:
            setattr(ps, "__QEMU", old)

    def test_irq_disable_bp_without_qemu(self):
        old = getattr(ps, "__QEMU", None)
        setattr(ps, "__QEMU", None)
        try:
            ps.irq_disable_bp(9)
        finally:
            setattr(ps, "__QEMU", old)


class TestTriggerInterrupt:
    def test_trigger_interrupt_calls_irq_set_qmp(self):
        mock_qemu = mock.Mock()
        old = getattr(ps, "__QEMU", None)
        setattr(ps, "__QEMU", mock_qemu)
        try:
            ps.trigger_interrupt(10)
            mock_qemu.irq_set_qmp.assert_called_once_with(10)
        finally:
            setattr(ps, "__QEMU", old)


class TestStop:
    def test_stop_sets_flag(self):
        old = getattr(ps, "__STOP_SERVER", None)
        setattr(ps, "__STOP_SERVER", False)
        ps.stop()
        assert getattr(ps, "__STOP_SERVER") is True
        setattr(ps, "__STOP_SERVER", old)


class TestRegRxHandler:
    def test_marks_function(self):
        @ps.reg_rx_handler
        def my_handler(msg):
            pass

        assert my_handler.is_rx_handler is True


class TestPeripheralModelDecorator:
    def test_registers_rx_handlers(self):
        # Save state
        saved = dict(getattr(ps, "__RX_HANDLERS__", {}))
        saved_socket = ps.__RX_SOCKET__

        # Clear RX socket to avoid setsockopt on stale socket from prior tests
        ps.__RX_SOCKET__ = None

        try:
            @ps.peripheral_model
            class TestModel:
                @classmethod
                @ps.reg_rx_handler
                def on_data(cls, msg):
                    pass

            key = "Peripheral.TestModel.on_data"
            rx_handlers = getattr(ps, "__RX_HANDLERS__", {})
            assert key in rx_handlers
            # Clean up
            if key in rx_handlers:
                del rx_handlers[key]
        finally:
            ps.__RX_SOCKET__ = saved_socket
