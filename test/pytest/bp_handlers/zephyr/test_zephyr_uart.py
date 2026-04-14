"""
Tests for halucinator.bp_handlers.zephyr.zephyr_uart
"""

from unittest import mock

import pytest

from halucinator.bp_handlers.zephyr.zephyr_uart import ZephyrUART


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_qemu_mock():
    """Build a mock QEMU target."""
    qemu = mock.Mock()
    qemu.get_arg = mock.Mock(side_effect=lambda idx: 0)
    return qemu


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def uart_handler():
    """Create a ZephyrUART with a mocked UARTPublisher."""
    mock_impl = mock.Mock()
    handler = ZephyrUART(impl=mock_impl)
    return handler


@pytest.fixture
def qemu():
    return make_qemu_mock()


# ---------------------------------------------------------------------------
# Tests: __init__
# ---------------------------------------------------------------------------

class TestInit:
    def test_default_fields(self):
        mock_impl = mock.Mock()
        handler = ZephyrUART(impl=mock_impl)
        assert handler.model is mock_impl
        assert handler.tx_buf == bytes([])
        assert handler.rx_buf == bytes([])
        assert handler.last_write_dev is None


# ---------------------------------------------------------------------------
# Tests: mcux_init
# ---------------------------------------------------------------------------

class TestMcuxInit:
    def test_returns_true_and_zero(self, uart_handler, qemu):
        ret_intercept, ret_val = uart_handler.mcux_init(qemu, 0x0)
        assert ret_intercept is True
        assert ret_val == 0


# ---------------------------------------------------------------------------
# Tests: get_statusflags
# ---------------------------------------------------------------------------

class TestGetStatusFlags:
    def test_returns_tx_empty(self, uart_handler, qemu):
        ret_intercept, ret_val = uart_handler.get_statusflags(qemu, 0x0)
        assert ret_intercept is True
        assert ret_val == 0x80


# ---------------------------------------------------------------------------
# Tests: handle_tx
# ---------------------------------------------------------------------------

class TestHandleTx:
    def test_writes_character(self, uart_handler, qemu):
        uart_dev = 0x1234
        p_char = ord('A')

        qemu.get_arg = mock.Mock(side_effect=lambda idx: [uart_dev, p_char][idx])

        ret_intercept, ret_val = uart_handler.handle_tx(qemu, 0x0)
        assert ret_intercept is True
        assert ret_val == 0
        uart_handler.model.write.assert_called_once_with(0, bytes([p_char]))
        assert uart_handler.last_write_dev == uart_dev


# ---------------------------------------------------------------------------
# Tests: handle_rx
# ---------------------------------------------------------------------------

class TestHandleRx:
    def test_reads_one_character_success(self, uart_handler, qemu):
        p_char = 0x2000

        qemu.get_arg = mock.Mock(side_effect=lambda idx: [0, p_char][idx])
        uart_handler.model.read.return_value = b'X'

        ret_intercept, ret_val = uart_handler.handle_rx(qemu, 0x0)
        assert ret_intercept is True
        assert ret_val == 0
        uart_handler.model.read.assert_called_once_with(0, 1, count=1, block=False)
        qemu.write_memory.assert_called_once_with(p_char, 1, b'X', 1, raw=True)

    def test_reads_no_data_returns_error(self, uart_handler, qemu):
        p_char = 0x2000

        qemu.get_arg = mock.Mock(side_effect=lambda idx: [0, p_char][idx])
        uart_handler.model.read.return_value = b''

        ret_intercept, ret_val = uart_handler.handle_rx(qemu, 0x0)
        assert ret_intercept is True
        assert ret_val == 0xFFFFFFFF
        qemu.write_memory.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: handle_rx_multiple
# ---------------------------------------------------------------------------

class TestHandleRxMultiple:
    def test_reads_multiple_characters(self, uart_handler, qemu):
        p_char = 0x2000
        count = 5
        data = b'ABCDE'

        qemu.get_arg = mock.Mock(side_effect=lambda idx: [0, p_char, count][idx])
        uart_handler.model.read.return_value = data

        ret_intercept, ret_val = uart_handler.handle_rx_multiple(qemu, 0x0)
        assert ret_intercept is True
        assert ret_val == 5
        uart_handler.model.read.assert_called_once_with(0, 1, count=count, block=False)
        qemu.write_memory.assert_called_once_with(p_char, 1, data, 5, raw=True)

    def test_reads_no_data_returns_zero(self, uart_handler, qemu):
        p_char = 0x2000
        count = 5

        qemu.get_arg = mock.Mock(side_effect=lambda idx: [0, p_char, count][idx])
        uart_handler.model.read.return_value = b''

        ret_intercept, ret_val = uart_handler.handle_rx_multiple(qemu, 0x0)
        assert ret_intercept is True
        assert ret_val == 0
        qemu.write_memory.assert_not_called()

    def test_reads_partial_data(self, uart_handler, qemu):
        p_char = 0x2000
        count = 10
        data = b'AB'

        qemu.get_arg = mock.Mock(side_effect=lambda idx: [0, p_char, count][idx])
        uart_handler.model.read.return_value = data

        ret_intercept, ret_val = uart_handler.handle_rx_multiple(qemu, 0x0)
        assert ret_intercept is True
        assert ret_val == 2
        qemu.write_memory.assert_called_once_with(p_char, 1, data, 2, raw=True)


# ---------------------------------------------------------------------------
# Tests: handle_rx_charptr (console_getline)
# ---------------------------------------------------------------------------

class TestHandleRxCharptr:
    def test_reads_line_and_writes_to_memory(self, uart_handler, qemu):
        # Setup avatar.config.memory_by_name
        mock_config = mock.Mock()
        mock_mem = mock.Mock()
        mock_mem.base_addr = 0x30000000
        mock_config.memory_by_name.return_value = mock_mem
        qemu.avatar = mock.Mock()
        qemu.avatar.config = mock_config

        # Simulate reading "hello\n" one byte at a time
        call_count = [0]
        chars = [b'h', b'e', b'l', b'l', b'o', b'\n']

        def read_side_effect(uart_id, count=1, block=True):
            idx = call_count[0]
            call_count[0] += 1
            if idx < len(chars):
                return chars[idx]
            return b'\n'

        uart_handler.model.read = mock.Mock(side_effect=read_side_effect)

        ret_intercept, ret_val = uart_handler.handle_rx_charptr(qemu, 0x0)
        assert ret_intercept is True
        assert ret_val == 0x30000000
        # rx_buf should be "hello\x00"
        assert uart_handler.rx_buf == b'hello\x00'
        qemu.write_memory.assert_called_once()

    def test_uses_default_address_when_no_memory(self, uart_handler, qemu):
        mock_config = mock.Mock()
        mock_config.memory_by_name.return_value = None
        qemu.avatar = mock.Mock()
        qemu.avatar.config = mock_config

        # Single newline = empty line
        call_count = [0]
        def read_side_effect(uart_id, count=1, block=True):
            return b'\n'

        uart_handler.model.read = mock.Mock(side_effect=read_side_effect)

        ret_intercept, ret_val = uart_handler.handle_rx_charptr(qemu, 0x0)
        assert ret_intercept is True
        # Default address is 0x30000000
        assert ret_val == 0x30000000
