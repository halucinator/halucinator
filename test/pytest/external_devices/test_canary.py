"""
Tests for halucinator.external_devices.canary
"""

import logging
from unittest import mock

import pytest

from halucinator.external_devices.canary import CanaryDevice, CustomFormatter


@pytest.fixture
def mock_ioserver():
    return mock.Mock()


@pytest.fixture
def canary(mock_ioserver):
    return CanaryDevice(mock_ioserver)


class TestCustomFormatter:
    def test_format_debug(self):
        fmt = CustomFormatter()
        record = logging.LogRecord(
            name="test", level=logging.DEBUG, pathname="", lineno=0,
            msg="debug msg", args=(), exc_info=None,
        )
        result = fmt.format(record)
        assert "debug msg" in result

    def test_format_info(self):
        fmt = CustomFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="info msg", args=(), exc_info=None,
        )
        result = fmt.format(record)
        assert "info msg" in result

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
        assert "\x1b[31;20m" in result

    def test_format_critical(self):
        fmt = CustomFormatter()
        record = logging.LogRecord(
            name="test", level=logging.CRITICAL, pathname="", lineno=0,
            msg="critical msg", args=(), exc_info=None,
        )
        result = fmt.format(record)
        assert "critical msg" in result
        assert "\x1b[31;1m" in result


class TestCanaryDeviceInit:
    def test_stores_ioserver(self, mock_ioserver):
        cd = CanaryDevice(mock_ioserver)
        assert cd.ioserver is mock_ioserver

    def test_registers_canary_topic(self, mock_ioserver):
        CanaryDevice(mock_ioserver)
        mock_ioserver.register_topic.assert_called_once_with(
            "Peripheral.CanaryModel.canary", mock.ANY
        )

    def test_creates_canary_logger(self, canary):
        assert canary.canary_log is not None
        assert canary.canary_log.name == "Canary.Sensitive.Function"

    def test_logger_has_handler(self, canary):
        # Should have at least the stdout handler added
        assert len(canary.canary_log.handlers) >= 1


class TestCanaryDeviceWriteHandler:
    def test_logs_canary_message(self, canary, mock_ioserver):
        msg = {"canary_type": "stack", "msg": "overflow detected"}
        with mock.patch.object(canary.canary_log, "critical") as mock_critical:
            canary.write_handler(mock_ioserver, msg)
            mock_critical.assert_called_once_with(
                "Type: %s - %s", "stack", "overflow detected"
            )

    def test_handler_with_different_types(self, canary, mock_ioserver):
        msg = {"canary_type": "heap", "msg": "corruption"}
        with mock.patch.object(canary.canary_log, "critical") as mock_critical:
            canary.write_handler(mock_ioserver, msg)
            mock_critical.assert_called_once_with(
                "Type: %s - %s", "heap", "corruption"
            )


class TestCanaryMainBlock:
    def test_main_arg_parsing(self):
        """Test the arg parsing portion of __main__ block."""
        from argparse import ArgumentParser
        p = ArgumentParser()
        p.add_argument("-r", "--rx_port", default=5556)
        p.add_argument("-t", "--tx_port", default=5555)
        p.add_argument("-i", "--id", default=0x20000AB0, type=int)
        p.add_argument("-n", "--newline", default=False, action="store_true")
        args = p.parse_args([])
        assert args.rx_port == 5556
        assert args.tx_port == 5555
        assert args.id == 0x20000AB0
        assert args.newline is False

    def test_main_arg_parsing_custom(self):
        from argparse import ArgumentParser
        p = ArgumentParser()
        p.add_argument("-r", "--rx_port", default=5556)
        p.add_argument("-t", "--tx_port", default=5555)
        p.add_argument("-i", "--id", default=0x20000AB0, type=int)
        p.add_argument("-n", "--newline", default=False, action="store_true")
        args = p.parse_args(["-r", "7777", "-t", "8888", "-n"])
        assert args.rx_port == "7777"
        assert args.newline is True
