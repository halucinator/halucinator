from unittest import mock

import pytest
import zmq
from peripheral_models_helpers import (
    PS_RX_PORT,
    PS_TX_PORT,
    fix_server_shutdown,
)

from halucinator.external_devices.ioserver import IOServer
from halucinator.peripheral_models import peripheral_server


def test_ioserver_shutdown_bug():
    """
    The IOServer.shutdown method does not close the sockets, which prevents
    re-creating the IOServer object with the same ports.
    """
    xfail_flag = False
    io_server = IOServer(rx_port=PS_TX_PORT, tx_port=PS_RX_PORT)
    io_server.shutdown()
    try:
        io_server = IOServer(rx_port=PS_TX_PORT, tx_port=PS_RX_PORT)
    # The expected exception.
    except zmq.error.ZMQError as ex:
        if "Address already in use" in str(ex):
            xfail_flag = True
        else:
            raise
    finally:
        assert not io_server.is_alive()
        # No need to call io_server.shutdown() as we have not started the thread.
        fix_server_shutdown(io_server.rx_socket, io_server.tx_socket, 0)
    # Xfail the test until it's fixed in the tested code.
    try:
        assert xfail_flag
        pytest.xfail("zmq.error.ZMQError: Address already in use")
    except:
        pytest.xfail(
            "For unknown reason, zmq.connect and zmq.bind "
            "do not complain about using a currently used address"
        )


def test_peripheral_server_stop_bug():
    """
    The peripheral_server.stop method does not close the sockets, which prevents
    re-starting the server with the same ports.
    """
    xfail_flag = False
    # Start a peripheral server.
    peripheral_server.start(
        rx_port=PS_RX_PORT,
        tx_port=PS_TX_PORT,
        qemu=mock.Mock(avatar=mock.Mock(output_directory=None)),
    )
    # Stop the peripheral server.
    peripheral_server.stop()
    try:
        # Restart the server with the same ports.
        peripheral_server.start(
            rx_port=PS_RX_PORT,
            tx_port=PS_TX_PORT,
            qemu=mock.Mock(avatar=mock.Mock(output_directory=None)),
        )
    # The expected exception.
    except zmq.error.ZMQError as ex:
        if "Address already in use" in str(ex):
            xfail_flag = True
        else:
            raise
    finally:
        # No need to call peripheral_server.stop() as we have not started the thread.
        fix_server_shutdown(
            peripheral_server.__rx_socket__, peripheral_server.__tx_socket__, 0
        )
    # Xfail the test until it's fixed in the tested code.
    try:
        assert xfail_flag
        pytest.xfail("zmq.error.ZMQError: Address already in use")
    except:
        pytest.xfail(
            "For unknown reason, zmq.connect and zmq.bind "
            "do not complain about using a currently used address"
        )
