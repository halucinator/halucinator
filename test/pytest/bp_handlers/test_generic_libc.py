"""Tests for halucinator.bp_handlers.generic.libc module."""

import logging
from unittest import mock

import pytest

from halucinator.bp_handlers.generic.libc import Libc6


@pytest.fixture
def qemu():
    m = mock.Mock()
    m.regs = mock.Mock()
    return m


@pytest.fixture
def hal_info(caplog):
    """Capture HAL_LOG records (firmware stdio + diagnostic warnings).

    The firmware-stdio fallback in Libc6 routes through hal_log.info / hal_log.warning;
    by default, halucinator's logging.cfg sets propagate=0 on HAL_LOG and may flag
    it `disabled=True` (via fileConfig's disable_existing_loggers), which hides
    records from pytest's root-level caplog handler. Force-enable propagation and
    DEBUG level for the duration of the test so caplog sees everything; restore
    on teardown."""
    hal_logger = logging.getLogger("HAL_LOG")
    saved_disabled = hal_logger.disabled
    saved_propagate = hal_logger.propagate
    hal_logger.disabled = False
    hal_logger.propagate = True
    caplog.set_level(logging.DEBUG, logger="HAL_LOG")
    yield caplog
    hal_logger.disabled = saved_disabled
    hal_logger.propagate = saved_propagate


ADDR = 0x1000


class TestPuts:
    def test_puts_reads_and_prints_string(self, qemu, hal_info):
        handler = Libc6()
        qemu.get_arg.return_value = 0x5000
        qemu.read_string.return_value = "Hello World"

        intercept, ret = handler.puts(qemu, ADDR)

        qemu.read_string.assert_called_once_with(0x5000)
        assert intercept is True
        assert ret == 1
        assert "Hello World" in hal_info.text


class TestPrintf:
    def test_printf_no_format_specifiers(self, qemu, hal_info):
        handler = Libc6()
        qemu.get_arg.side_effect = lambda i: 0x5000 if i == 0 else 0
        qemu.read_string.return_value = "plain text"

        intercept, ret = handler.printf(qemu, ADDR)

        assert intercept is True
        assert ret == len("plain text")
        assert "plain text" in hal_info.text

    def test_printf_with_int_format(self, qemu, hal_info):
        handler = Libc6()

        def get_arg(i):
            if i == 0:
                return 0x5000
            elif i == 1:
                return 42
            return 0

        qemu.get_arg.side_effect = get_arg
        qemu.read_string.return_value = "value=%d"

        intercept, ret = handler.printf(qemu, ADDR)

        assert intercept is True
        assert "value=42" in hal_info.text

    def test_printf_with_hex_format(self, qemu, hal_info):
        handler = Libc6()

        def get_arg(i):
            if i == 0:
                return 0x5000
            elif i == 1:
                return 255
            return 0

        qemu.get_arg.side_effect = get_arg
        qemu.read_string.return_value = "hex=%x"

        intercept, ret = handler.printf(qemu, ADDR)
        assert intercept is True
        assert "hex=ff" in hal_info.text

    def test_printf_with_string_format(self, qemu, hal_info):
        handler = Libc6()

        def get_arg(i):
            if i == 0:
                return 0x5000
            elif i == 1:
                return 0x6000
            return 0

        qemu.get_arg.side_effect = get_arg
        # First call for the format string, second for %s arg
        qemu.read_string.side_effect = ["msg=%s", "hello"]

        intercept, ret = handler.printf(qemu, ADDR)
        assert intercept is True
        assert "msg=hello" in hal_info.text

    def test_printf_with_unsigned_int(self, qemu, hal_info):
        handler = Libc6()

        def get_arg(i):
            if i == 0:
                return 0x5000
            elif i == 1:
                return 99
            return 0

        qemu.get_arg.side_effect = get_arg
        qemu.read_string.return_value = "val=%u"

        intercept, ret = handler.printf(qemu, ADDR)
        assert intercept is True
        assert "val=99" in hal_info.text

    def test_printf_with_octal(self, qemu, hal_info):
        handler = Libc6()

        def get_arg(i):
            if i == 0:
                return 0x5000
            elif i == 1:
                return 8
            return 0

        qemu.get_arg.side_effect = get_arg
        qemu.read_string.return_value = "oct=%o"

        intercept, ret = handler.printf(qemu, ADDR)
        assert intercept is True

    def test_printf_unhandled_format(self, qemu, hal_info):
        """Unhandled format specifiers log a single WARNING to HAL_LOG
        (was two print() lines: 'Unhandled format' + 'format: ...')."""
        handler = Libc6()

        def get_arg(i):
            if i == 0:
                return 0x5000
            elif i == 1:
                return 0
            return 0

        qemu.get_arg.side_effect = get_arg
        qemu.read_string.return_value = "val=%z"

        intercept, ret = handler.printf(qemu, ADDR)
        assert intercept is True
        assert ret == 1
        warnings = [r for r in hal_info.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "Unhandled printf format %z" in warnings[0].getMessage()
        assert "val=%z" in warnings[0].getMessage()


class TestExit:
    def test_exit_calls_shutdown(self, qemu):
        handler = Libc6()
        qemu.get_arg.return_value = 0

        intercept, ret = handler.halucinator_exit(qemu, ADDR)

        qemu.halucinator_shutdown.assert_called_once_with(0)
        assert intercept is False
        assert ret is None

    def test_exit_masks_to_byte(self, qemu):
        handler = Libc6()
        qemu.get_arg.return_value = 0x1FF  # should mask to 0xFF

        handler.halucinator_exit(qemu, ADDR)

        qemu.halucinator_shutdown.assert_called_once_with(0xFF)


class TestPrintfFloatFormats:
    """Test float/double format specifiers in printf (lines 48, 56, 58, 62)."""

    def test_printf_with_float_f(self, qemu, hal_info):
        handler = Libc6()

        def get_arg(i):
            if i == 0:
                return 0x5000
            elif i == 1:
                return 3.14
            return 0

        qemu.get_arg.side_effect = get_arg
        qemu.read_string.return_value = "val=%f"

        intercept, ret = handler.printf(qemu, ADDR)
        assert intercept is True
        assert "3.14" in hal_info.text

    def test_printf_with_float_e(self, qemu, hal_info):
        handler = Libc6()

        def get_arg(i):
            if i == 0:
                return 0x5000
            elif i == 1:
                return 1.5
            return 0

        qemu.get_arg.side_effect = get_arg
        qemu.read_string.return_value = "val=%e"

        intercept, ret = handler.printf(qemu, ADDR)
        assert intercept is True

    def test_printf_with_float_g(self, qemu, hal_info):
        handler = Libc6()

        def get_arg(i):
            if i == 0:
                return 0x5000
            elif i == 1:
                return 2.5
            return 0

        qemu.get_arg.side_effect = get_arg
        qemu.read_string.return_value = "val=%g"

        intercept, ret = handler.printf(qemu, ADDR)
        assert intercept is True

    def test_printf_with_float_a(self, qemu, hal_info):
        handler = Libc6()

        def get_arg(i):
            if i == 0:
                return 0x5000
            elif i == 1:
                return 1.0
            return 0

        qemu.get_arg.side_effect = get_arg
        qemu.read_string.return_value = "val=%a"

        intercept, ret = handler.printf(qemu, ADDR)
        assert intercept is True

    def test_printf_with_char_c(self, qemu, hal_info):
        handler = Libc6()

        def get_arg(i):
            if i == 0:
                return 0x5000
            elif i == 1:
                return 0x6000
            return 0

        qemu.get_arg.side_effect = get_arg
        qemu.read_string.side_effect = ["char=%c", "A"]

        intercept, ret = handler.printf(qemu, ADDR)
        assert intercept is True
        assert "char=A" in hal_info.text


class TestFmtIdx:
    """register_handler stores a per-intercept-address format-string
    argument index, used by printf for variants like SEGGER_RTT_printf
    where fmt isn't at arg 0."""

    def test_register_handler_default_fmt_idx_is_zero(self, qemu):
        handler = Libc6()
        handler.register_handler(qemu, ADDR, "printf")
        assert handler._fmt_idx[ADDR] == 0

    def test_register_handler_records_fmt_idx_per_address(self, qemu):
        handler = Libc6()
        handler.register_handler(qemu, 0x1000, "printf", fmt_idx=0)
        handler.register_handler(qemu, 0x2000, "SEGGER_RTT_printf", fmt_idx=1)
        handler.register_handler(qemu, 0x3000, "snprintf", fmt_idx=2)
        assert handler._fmt_idx == {0x1000: 0, 0x2000: 1, 0x3000: 2}

    def test_printf_reads_fmt_from_configured_arg_index(self, qemu, hal_info):
        """fmt_idx=1 → printf should read the format string from arg 1, not arg 0."""
        handler = Libc6()
        handler.register_handler(qemu, ADDR, "SEGGER_RTT_printf", fmt_idx=1)

        def get_arg(i):
            return {0: 0xC0DE, 1: 0x5000, 2: 42}.get(i, 0)

        qemu.get_arg.side_effect = get_arg
        qemu.read_string.return_value = "shifted=%d"

        intercept, ret = handler.printf(qemu, ADDR)

        assert intercept is True
        # fmt was read from arg 1, va-args started at arg 2 → 42
        qemu.read_string.assert_called_once_with(0x5000)
        assert "shifted=42" in hal_info.text

    def test_printf_unconfigured_address_falls_back_to_idx_zero(self, qemu, hal_info):
        """If register_handler was never called for this addr, printf reads
        from arg 0 (matches plain C printf)."""
        handler = Libc6()
        qemu.get_arg.side_effect = lambda i: 0x5000 if i == 0 else 0
        qemu.read_string.return_value = "no register call"
        intercept, ret = handler.printf(qemu, 0xDEAD)
        assert intercept is True
        assert "no register call" in hal_info.text


class TestStdioRouting:
    """Libc6 mirrors printf/puts output through Peripheral.UTTYModel.tx_buf
    so external devices can subscribe over ZMQ. Falls back to print() when
    publishing fails (interface not registered, no subscribers, etc.)."""

    def test_register_stdio_interface_returns_id_on_success(self):
        """When UTTYModel.add_interface succeeds, _stdio_iface is set."""
        with mock.patch("halucinator.peripheral_models.utty.UTTYModel.add_interface"):
            handler = Libc6()
            assert handler._stdio_iface == "STDIO"

    def test_register_stdio_interface_returns_none_on_failure(self):
        """When UTTYModel.add_interface raises, _stdio_iface stays None and
        we never try to publish."""
        with mock.patch(
            "halucinator.peripheral_models.utty.UTTYModel.add_interface",
            side_effect=RuntimeError("not initialised"),
        ):
            handler = Libc6()
            assert handler._stdio_iface is None

    def test_publish_stdio_uses_utty_when_iface_set(self, qemu):
        """When _stdio_iface is set and UTTYModel.tx_buf succeeds, printf
        output is published via tx_buf and NOT via print()."""
        handler = Libc6()
        handler._stdio_iface = "STDIO"
        with mock.patch(
            "halucinator.peripheral_models.utty.UTTYModel.tx_buf"
        ) as mock_tx:
            result = handler._publish_stdio("hello")
            assert result is True
            mock_tx.assert_called_once_with("STDIO", b"hello")

    def test_publish_stdio_returns_false_when_iface_none(self):
        """No interface → no publish; caller falls back to print()."""
        handler = Libc6()
        handler._stdio_iface = None
        assert handler._publish_stdio("anything") is False

    def test_publish_stdio_returns_false_on_exception(self):
        """tx_buf raising (e.g. no subscriber bound) is swallowed; printf
        falls back to print() so output remains visible."""
        handler = Libc6()
        handler._stdio_iface = "STDIO"
        with mock.patch(
            "halucinator.peripheral_models.utty.UTTYModel.tx_buf",
            side_effect=Exception("zmq not ready"),
        ):
            assert handler._publish_stdio("hello") is False

    def test_printf_publishes_through_utty_when_available(self, qemu):
        """When STDIO routing is wired up, printf publishes via UTTYModel
        rather than print()ing to halucinator's stdout."""
        handler = Libc6()
        handler._stdio_iface = "STDIO"
        qemu.get_arg.side_effect = lambda i: 0x5000 if i == 0 else 0
        qemu.read_string.return_value = "via UTTY"
        with mock.patch(
            "halucinator.peripheral_models.utty.UTTYModel.tx_buf"
        ) as mock_tx:
            handler.printf(qemu, ADDR)
            mock_tx.assert_called_once_with("STDIO", b"via UTTY")

    def test_subclass_can_disable_stdio_routing(self):
        """Setting _STDIO_INTERFACE_ID = None in a subclass restores the
        plain print() behaviour."""
        class Libc6NoStdio(Libc6):
            _STDIO_INTERFACE_ID = None

        handler = Libc6NoStdio()
        assert handler._stdio_iface is None
