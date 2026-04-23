"""Unit tests for halucinator.peripheral_models.canary module."""

import logging
from unittest import mock

import pytest

from halucinator.peripheral_models.canary import CanaryModel, CustomFormatter


class TestCustomFormatter:
    def test_format_debug(self):
        fmt = CustomFormatter()
        record = logging.LogRecord(
            name="test", level=logging.DEBUG, pathname="", lineno=0,
            msg="debug msg", args=(), exc_info=None,
        )
        result = fmt.format(record)
        assert "debug msg" in result

    def test_format_warning(self):
        fmt = CustomFormatter()
        record = logging.LogRecord(
            name="test", level=logging.WARNING, pathname="", lineno=0,
            msg="warn msg", args=(), exc_info=None,
        )
        result = fmt.format(record)
        assert "warn msg" in result
        assert "\x1b[33;20m" in result

    def test_format_error(self):
        fmt = CustomFormatter()
        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname="", lineno=0,
            msg="error msg", args=(), exc_info=None,
        )
        result = fmt.format(record)
        assert "error msg" in result

    def test_format_critical(self):
        fmt = CustomFormatter()
        record = logging.LogRecord(
            name="test", level=logging.CRITICAL, pathname="", lineno=0,
            msg="critical msg", args=(), exc_info=None,
        )
        result = fmt.format(record)
        assert "critical msg" in result
        assert "\x1b[31;1m" in result

    def test_format_unknown_level(self):
        fmt = CustomFormatter()
        record = logging.LogRecord(
            name="test", level=99, pathname="", lineno=0,
            msg="unknown msg", args=(), exc_info=None,
        )
        # Should not crash even with unknown level
        result = fmt.format(record)
        assert "unknown msg" in result


class TestCanaryModel:
    def test_canary_returns_dict(self):
        """Test canary method returns the expected dictionary (covers lines 62-77)."""
        from halucinator.peripheral_models import peripheral_server as ps
        orig_socket = getattr(ps, "__TX_SOCKET__", None)
        setattr(ps, "__TX_SOCKET__", mock.Mock())
        try:
            qemu = mock.Mock()
            qemu.get_symbol_name.return_value = "vuln_function"
            CanaryModel.canary(qemu, 0x1000, "StackOverflow", "detected!")
            tx_socket = getattr(ps, "__TX_SOCKET__")
            tx_socket.send_string.assert_called_once()
            call_str = tx_socket.send_string.call_args[0][0]
            assert "CanaryModel" in call_str
            assert "canary" in call_str
        finally:
            setattr(ps, "__TX_SOCKET__", orig_socket)

    def test_canary_interfaces_empty(self):
        assert CanaryModel.interfaces == {}
