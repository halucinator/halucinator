import time
from unittest import mock

import pytest
from peripheral_models_helpers import (
    PS_RX_PORT,
    PS_TX_PORT,
    fix_server_shutdown,
    setup_ioserver_device,
    wait_assert,
)

from halucinator.peripheral_models import peripheral_server


@peripheral_server.peripheral_model
class MockPeripheralModel:
    @classmethod
    @peripheral_server.tx_msg
    def write(cls):
        return {"key": "val"}


mock_decode_zmq_msg = mock.Mock(
    return_value=("Peripheral.MockPeripheralModel.write", {"key": "val"})
)


@pytest.fixture(scope="module", autouse=True)
def setup_io_server():
    with mock.patch(
        "halucinator.external_devices.ioserver.decode_zmq_msg",
        mock_decode_zmq_msg,
    ):
        yield from setup_ioserver_device()


class XfailException(Exception):
    """
    Custom exception indicating xfailure.
    """

    pass


@pytest.mark.xfail(
    raises=XfailException,
    reason="Missing peripheral server post-start delay results in a lost message",
)
def test_lost_message_without_post_init_delay():
    mock_decode_zmq_msg.reset_mock()
    peripheral_server.start(
        rx_port=PS_RX_PORT,
        tx_port=PS_TX_PORT,
        qemu=mock.Mock(avatar=mock.Mock(output_directory=None)),
    )
    time.sleep(0)
    MockPeripheralModel.write()
    try:
        wait_assert(lambda: mock_decode_zmq_msg.assert_called_once())
    except AssertionError as ex:
        if (
            str(ex)
            == "Expected 'mock' to have been called once. Called 0 times."
        ):
            raise XfailException from ex
        else:
            raise
    finally:
        fix_server_shutdown(
            peripheral_server.__rx_socket__,
            peripheral_server.__tx_socket__,
            0,
        )


def test_got_message_with_post_init_delay(setup_io_server):
    ioserver = setup_io_server
    ioserver.register_topic(
        "Peripheral.MockPeripheralModel.write", lambda *args: None
    )
    mock_decode_zmq_msg.reset_mock()
    peripheral_server.start(
        rx_port=PS_RX_PORT,
        tx_port=PS_TX_PORT,
        qemu=mock.Mock(avatar=mock.Mock(output_directory=None)),
    )
    time.sleep(0.2)
    MockPeripheralModel.write()
    wait_assert(lambda: mock_decode_zmq_msg.assert_called_once())
    fix_server_shutdown(
        peripheral_server.__rx_socket__, peripheral_server.__tx_socket__, 0,
    )
