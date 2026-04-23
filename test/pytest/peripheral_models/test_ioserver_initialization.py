import time
from unittest import mock

import pytest
from peripheral_models_helpers import (
    SetupPeripheralServer,
    fix_server_shutdown,
    wait_assert,
)

from halucinator.external_devices.ioserver import IOServer
from halucinator.peripheral_models import peripheral_server

mock_decode_zmq_msg = mock.Mock(return_value=("topic", {"key": "val"}))


@pytest.fixture(scope="module", autouse=True)
def setup_peripheral_server():

    with mock.patch.object(
        peripheral_server, "decode_zmq_msg", mock_decode_zmq_msg
    ):
        yield from SetupPeripheralServer.setup_peripheral_server()


class XfailException(Exception):
    """
    Custom exception indicating xfailure.
    """

    pass


@pytest.mark.xfail(
    raises=XfailException,
    reason="Missing IOServer post-initialization delay results in a lost message",
)
def test_lost_message_without_post_init_delay():
    mock_decode_zmq_msg.reset_mock()
    io_server = IOServer()
    io_server.send_msg("topic", {"key": "val"})
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
        fix_server_shutdown(io_server.rx_socket, io_server.tx_socket, 0)


def test_got_message_with_post_init_delay():
    mock_decode_zmq_msg.reset_mock()
    io_server = IOServer()
    time.sleep(0.2)
    io_server.send_msg("topic", {"key": "val"})
    wait_assert(lambda: mock_decode_zmq_msg.assert_called_once())
