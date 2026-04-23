"""
Unit tests for halucinator.peripheral_models.uart.UARTPublisher

These complement the existing test_uart.py integration tests by covering
methods directly without requiring ZMQ infrastructure.
"""
from collections import defaultdict, deque
from unittest import mock

import pytest

from halucinator.peripheral_models.uart import UARTPublisher


@pytest.fixture(autouse=True)
def clean_state():
    UARTPublisher.rx_buffers = defaultdict(deque)
    yield


# ---------- write ----------

@mock.patch("halucinator.peripheral_models.peripheral_server.__TX_SOCKET__")
def test_write(mock_socket):
    mock_socket.send_string = mock.Mock()
    UARTPublisher.write(0, b"hello")
    mock_socket.send_string.assert_called_once()
    call_arg = mock_socket.send_string.call_args[0][0]
    assert "UARTPublisher.write" in call_arg


@mock.patch("halucinator.peripheral_models.peripheral_server.__TX_SOCKET__")
def test_write_multiple_ids(mock_socket):
    mock_socket.send_string = mock.Mock()
    UARTPublisher.write(0, b"msg0")
    UARTPublisher.write(1, b"msg1")
    assert mock_socket.send_string.call_count == 2


# ---------- rx_data ----------

def test_rx_data():
    msg = {"id": 0, "chars": "hello"}
    UARTPublisher.rx_data(msg)
    assert list(UARTPublisher.rx_buffers[0]) == list("hello")


def test_rx_data_multiple():
    UARTPublisher.rx_data({"id": 0, "chars": "ab"})
    UARTPublisher.rx_data({"id": 0, "chars": "cd"})
    assert list(UARTPublisher.rx_buffers[0]) == list("abcd")


def test_rx_data_multiple_ids():
    UARTPublisher.rx_data({"id": 0, "chars": "hello"})
    UARTPublisher.rx_data({"id": 1, "chars": "world"})
    assert len(UARTPublisher.rx_buffers[0]) == 5
    assert len(UARTPublisher.rx_buffers[1]) == 5


# ---------- read ----------

def test_read_enough_data():
    UARTPublisher.rx_buffers[0].extend("hello")
    result = UARTPublisher.read(uart_id=0, count=3, block=False)
    assert result == b"hel"
    assert list(UARTPublisher.rx_buffers[0]) == list("lo")


def test_read_not_enough_data():
    UARTPublisher.rx_buffers[0].extend("hi")
    result = UARTPublisher.read(uart_id=0, count=5, block=False)
    assert result == b"hi"
    assert len(UARTPublisher.rx_buffers[0]) == 0


def test_read_empty():
    result = UARTPublisher.read(uart_id=0, count=5, block=False)
    assert result == b""


def test_read_exact_count():
    UARTPublisher.rx_buffers[0].extend("abc")
    result = UARTPublisher.read(uart_id=0, count=3, block=False)
    assert result == b"abc"
    assert len(UARTPublisher.rx_buffers[0]) == 0


# ---------- read_line ----------

def test_read_line_enough_data():
    UARTPublisher.rx_buffers[0].extend("hello\nworld")
    result = UARTPublisher.read_line(uart_id=0, count=20, block=False)
    assert result == b"hello\nworld"


def test_read_line_not_enough_data():
    UARTPublisher.rx_buffers[0].extend("hi")
    result = UARTPublisher.read_line(uart_id=0, count=5, block=False)
    assert result == b"hi"


def test_read_line_exact_count():
    UARTPublisher.rx_buffers[0].extend("ab\n")
    result = UARTPublisher.read_line(uart_id=0, count=3, block=False)
    assert result == b"ab\n"


def test_read_line_empty():
    result = UARTPublisher.read_line(uart_id=0, count=5, block=False)
    assert result == b""
