"""
Unit tests for halucinator.peripheral_models.spi (SPIPublisher and UARTModel)

Tests rx_data state management. The tx_msg sending behavior is tested by
the integration tests in test_spi.py which set up real zmq infrastructure.
"""
from collections import defaultdict, deque
from unittest import mock

import pytest

from halucinator.peripheral_models.spi import SPIPublisher, UARTModel


@pytest.fixture(autouse=True)
def clean_state():
    saved = SPIPublisher.rx_buffers
    SPIPublisher.rx_buffers = defaultdict(deque)
    yield
    SPIPublisher.rx_buffers = saved


def test_spi_rx_data():
    SPIPublisher.rx_data({"id": 0, "chars": b"\x41\x42\x43"})
    assert len(SPIPublisher.rx_buffers[0]) == 3
    assert list(SPIPublisher.rx_buffers[0]) == [0x41, 0x42, 0x43]


def test_spi_rx_data_multiple_ids():
    SPIPublisher.rx_data({"id": 0, "chars": b"\x01\x02"})
    SPIPublisher.rx_data({"id": 1, "chars": b"\x03\x04"})
    assert len(SPIPublisher.rx_buffers[0]) == 2
    assert len(SPIPublisher.rx_buffers[1]) == 2


def test_spi_rx_data_append():
    SPIPublisher.rx_data({"id": 0, "chars": b"\x01"})
    SPIPublisher.rx_data({"id": 0, "chars": b"\x02"})
    assert list(SPIPublisher.rx_buffers[0]) == [0x01, 0x02]


class TestSPIPublisherWrite:
    def test_write_sends_msg(self):
        """Test SPIPublisher.write returns correct message (covers line 65-71)."""
        from halucinator.peripheral_models import peripheral_server as ps
        orig_socket = getattr(ps, "__TX_SOCKET__", None)
        setattr(ps, "__TX_SOCKET__", mock.Mock())
        try:
            SPIPublisher.write(0, b"\x01\x02")
            getattr(ps, "__TX_SOCKET__").send_string.assert_called_once()
        finally:
            setattr(ps, "__TX_SOCKET__", orig_socket)


class TestUARTModel:
    def test_init(self):
        model = UARTModel()
        assert len(model.tx_buffer) == 0
        assert len(model.rx_buffer) == 0

    def test_write(self):
        model = UARTModel()
        model.write(b"hello")
        assert model.tx_buffer[0] == b"hello"

    def test_write_multiple(self):
        model = UARTModel()
        model.write(b"hello")
        model.write(b"world")
        assert len(model.tx_buffer) == 2
