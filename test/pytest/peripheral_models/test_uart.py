import logging
from threading import Thread
from unittest import mock

import pytest
from peripheral_models_helpers import (
    EXPECTED_INIT_UARTPUBLISHER_RX_BUFFERS,
    SetupPeripheralServer,
    device_send_to_UARTPublisher,
    do_test_receive_from_UARTPublisher,
    do_test_send_to_UARTPublisher,
    join_timeout,
    setup_ioserver_device,
)

from halucinator.external_devices.uart import UARTPrintServer
from halucinator.peripheral_models.uart import UARTPublisher


@pytest.fixture(scope="module", autouse=True)
def setup_peripheral_server():
    yield from SetupPeripheralServer.setup_peripheral_server()


@pytest.fixture(scope="module", autouse=True)
def setup_uart():
    yield from setup_ioserver_device(UARTPrintServer)


def test_receive_from_UARTPublisher(setup_uart):
    do_test_receive_from_UARTPublisher(setup_uart.device, 0.1)


def test_send_to_UARTPublisher(setup_uart):
    UARTPublisher.rx_buffers.clear()
    do_test_send_to_UARTPublisher(setup_uart.device, 0.1)


@pytest.mark.parametrize(
    "uart_id, count, block",
    [
        (0, len(EXPECTED_INIT_UARTPUBLISHER_RX_BUFFERS[0]) + 1, False),
        (1, 1, True),
    ],
)
def test_UARTPublisher_read_popsleft_rx_buffers(
    setup_uart, uart_id, count, block
):
    UARTPublisher.rx_buffers.clear()
    do_test_send_to_UARTPublisher(setup_uart.device, 0.1)
    assert (
        dict(UARTPublisher.rx_buffers)
        == EXPECTED_INIT_UARTPUBLISHER_RX_BUFFERS
    )
    popped_data = UARTPublisher.read(uart_id=uart_id, count=count, block=block)
    assert (
        popped_data
        == "".join(
            list(EXPECTED_INIT_UARTPUBLISHER_RX_BUFFERS[uart_id])[:count]
        ).encode()
    )
    assert (
        list(UARTPublisher.rx_buffers[uart_id])
        == list(EXPECTED_INIT_UARTPUBLISHER_RX_BUFFERS[uart_id])[count:]
    )


def test_uart_publisher_read_is_blocked(setup_uart):
    class UARTPublisherMockRead:
        """
        Trace calls to logging.Logger.debug in UARTPublisher.read to demonstrate
        blocking, and record the return value.
        """

        logger_debug = mock.Mock()
        read_rv = None

        @classmethod
        def read(cls, uart_id, count, block):
            with mock.patch.object(logging.Logger, "debug", cls.logger_debug):
                cls.read_rv = UARTPublisher.read(uart_id, count, block)

    # Start UARTPublisher.read in a thread and observe it blocked.
    uart_id = 0
    expected_rx_buffers = EXPECTED_INIT_UARTPUBLISHER_RX_BUFFERS[uart_id]
    count = len(expected_rx_buffers)
    read_thread = Thread(
        target=UARTPublisherMockRead.read, args=(uart_id, count, True)
    )
    UARTPublisher.rx_buffers.clear()
    UARTPublisherMockRead.logger_debug.reset_mock()
    read_thread.start()
    # One second is more than sufficient for UARTPublisher.read to pass its wait
    # loop, unless it's blocked.
    read_thread.join(timeout=1)
    # Check that UARTPublisher.read is still blocked, given that the two
    # Logger.debug calls in UARTPublisher.read surround the loop.
    assert read_thread.is_alive()
    UARTPublisherMockRead.logger_debug.assert_called_once()
    # Send messages for UARTPublisher to read.
    assert UARTPublisherMockRead.read_rv is None
    device_send_to_UARTPublisher(setup_uart.device)
    # Observe the thread complete.
    join_timeout(read_thread)
    # Check that 'count' characters were read.
    assert len(UARTPublisherMockRead.read_rv) == count
