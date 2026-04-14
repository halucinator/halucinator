import socket
from collections import deque
from time import sleep
from unittest import mock

import pytest

from halucinator.peripheral_models.tcp_stack import TCPModel

TCPMODEL_PORT = 8888


@pytest.fixture(scope="module", autouse=True)
def setup_TCPModel():
    server = TCPModel()
    server.listen(TCPMODEL_PORT)
    # Let server start to wait for a client connection.
    sleep(0.01)
    yield server
    # Reset the listening loop condition.
    server._shutdown.set()
    # Let server wait for a client connection, if applicable.
    sleep(0.01)
    if server.is_alive():
        # Server must be waiting on client connection inside the listening
        # loop. Connect a client to break the loop.
        with socket.socket() as client:
            client.connect(("", TCPMODEL_PORT))
    sleep(0.01)
    assert not server.is_alive()
    server.sock.close()


def test_get_rx_packet_on_empty_queue_returns_None(setup_TCPModel):
    server = setup_TCPModel
    # There is no TCPModel method to clear packet_queue.
    server.packet_queue.clear()
    assert server.get_rx_packet() is None


def receive_packets_from_client(server, packets):
    with socket.socket() as client:
        client.connect(("", TCPMODEL_PORT))
        for packet in packets:
            client.send(packet)
            # Without a delay between sent packets they are received concatenated.
            sleep(0.01)


def test_get_rx_packet_on_nonempy_queue_leftpops_queue(setup_TCPModel):
    server = setup_TCPModel
    packets = [b"packet1", b"packet2"]
    server.packet_queue.clear()
    receive_packets_from_client(server, packets)
    assert server.get_rx_packet() == packets[0]
    assert server.packet_queue == deque(packets[1:])


def test_tx_packet_without_client_logs_critical(setup_TCPModel):
    server = setup_TCPModel
    with mock.patch("halucinator.peripheral_models.tcp_stack.log.critical") as mock_critical:
        server.tx_packet(b"packet")
    mock_critical.assert_called_once_with(
        "Trying to send data when there's no connected client!"
    )


def test_tx_packet_with_client_delivers_packet(setup_TCPModel):
    server = setup_TCPModel
    with socket.socket() as client:
        client.connect(("", TCPMODEL_PORT))
        # Let server set client connection before sending packet to client.
        sleep(0.01)
        packet = b"packet"
        server.tx_packet(packet)
        assert client.recv(len(packet)) == packet


def test_listen_receives_client_packets(setup_TCPModel):
    server = setup_TCPModel
    server.packet_queue.clear()
    packets = [b"packet1", b"packet2"]
    receive_packets_from_client(server, packets)
    assert list(server.packet_queue) == packets
