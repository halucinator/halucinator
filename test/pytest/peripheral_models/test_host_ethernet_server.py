import os
import time
from collections import defaultdict
from unittest import mock

import pytest
import zmq

pytestmark = pytest.mark.skipif(
    os.geteuid() != 0, reason="requires root privileges for raw sockets"
)
from peripheral_models_helpers import (
    PS_RX_PORT,
    PS_TX_PORT,
    SetupPeripheralServer,
    join_timeout,
)

import halucinator.external_devices.host_ethernet_server as HES
from halucinator.peripheral_models.ethernet import (
    EthernetMessage,
    EthernetModel,
)


class RegisteredIOServer(HES.IOServer):
    def __init__(self, rx_port, tx_port):
        super().__init__(rx_port, tx_port)
        self.received_data = []
        # Calling zmq.Socket.setsockopt from HES.IOServer.register_topic results
        # in TypeError: unicode not allowed, use setsockopt_string.
        #
        # TODO: Remove the mock once the bug is fixed.
        with mock.patch("zmq.Socket.setsockopt", zmq.Socket.setsockopt_string):
            self.register_topic(
                "Peripheral.EthernetModel.tx_frame", self.__class__.receive
            )

    def receive(self, data):
        self.received_data.append(data)


@pytest.fixture(scope="module", autouse=True)
def setup_peripheral_server():
    yield from SetupPeripheralServer.setup_peripheral_server()


@pytest.fixture(scope="module", autouse=True)
def setup_registered_ioserver():
    # Start ioserver
    ioserver = RegisteredIOServer(PS_TX_PORT, PS_RX_PORT)
    ioserver.start()
    time.sleep(0.2)
    yield ioserver
    # Shutdown ioserver
    ioserver.shutdown()
    # Since HES.IOServer.run loop uses a blocking call of
    # zmq.Socket.recv_string, a message from a client may be needed before the
    # ioserver thread can join.
    random_interface_id = 123
    ctrl_msg = EthernetMessage(
        interface_id=random_interface_id,
        frame=b"control message to ensure server shutdown",
    )
    if ioserver.is_alive():
        EthernetModel.tx_frame(ctrl_msg["interface_id"], ctrl_msg["frame"])
    join_timeout(ioserver)
    # ioserver sockets needs to close after the tread joined, so that another
    # server can connect/bind to the same ports.
    ioserver.rx_socket.close()
    ioserver.tx_socket.close()


N_IIDS = 2
N_MSGS = 2


def message(iid, msg_num):
    return EthernetMessage(
        interface_id=iid,
        frame=f"message #{msg_num} to/from iid {iid}".encode(),
    )


MSG_LIST = [
    message(iid, msg_num) for iid in range(N_IIDS) for msg_num in range(N_MSGS)
]


def test_ioserver_run(setup_registered_ioserver):
    ioserver = setup_registered_ioserver
    assert ioserver.received_data == []

    for msg in MSG_LIST:
        EthernetModel.tx_frame(msg["interface_id"], msg["frame"])
    time.sleep(0.2)

    assert ioserver.received_data == MSG_LIST


def test_ioserver_send_msg(setup_registered_ioserver):
    ioserver = setup_registered_ioserver

    EthernetModel.frame_queues.clear()
    EthernetModel.frame_times.clear()

    topic = "Peripheral.EthernetModel.rx_frame"
    sent_times = defaultdict(list)
    for msg in MSG_LIST:
        ioserver.send_msg(topic, msg)
        sent_times[msg["interface_id"]].append(time.time())
    time.sleep(0.2)

    assert set(EthernetModel.frame_queues.keys()) == set(
        [msg["interface_id"] for msg in MSG_LIST]
    )
    for iid in EthernetModel.frame_queues:
        assert list(EthernetModel.frame_queues[iid]) == [
            msg["frame"] for msg in MSG_LIST if msg["interface_id"] == iid
        ]

    assert set(EthernetModel.frame_times.keys()) == set(
        [msg["interface_id"] for msg in MSG_LIST]
    )
    for iid in EthernetModel.frame_times:
        assert len(EthernetModel.frame_times[iid]) == len(sent_times[iid])
        for (ts_received, ts_sent) in zip(
            list(EthernetModel.frame_times[iid]), sent_times[iid]
        ):
            try:
                assert ts_received >= ts_sent
            except:
                assert ts_received >= ts_sent - 0.05
                pytest.xfail("time.time() is not always monotonic")
            assert ts_received - ts_sent < 0.2
