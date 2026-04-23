import os
import time
from collections import defaultdict
from unittest import mock

import pytest
import scapy.all as scapy

pytestmark = pytest.mark.skipif(
    os.geteuid() != 0, reason="requires root privileges for raw sockets"
)
from peripheral_models_helpers import (
    PS_RX_PORT,
    PS_TX_PORT,
    SetupPeripheralServer,
    assert_,
    join_timeout,
    wait_assert,
)

from halucinator.external_devices.ethernet_virt_hub import (
    HostEthernetServer,
    ViruatalEthHub,
)
from halucinator.external_devices.ioserver import IOServer
from halucinator.external_devices.trigger_interrupt import SendInterrupt
from halucinator.peripheral_models.ethernet import (
    EthernetMessage,
    EthernetModel,
)

N_IIDS = 2


class HandledEthernetFrames:
    """
    This class is used to capture ethernet frames handled by HostEthernetServer.run.
    """

    def __init__(self, ethserver):
        self.ethserver = ethserver
        self.handler = ethserver.handler
        assert self.handler is not None
        self.handled_frames = []

    def mock_handler(self, server, msg):
        if server == self.ethserver:
            self.handled_frames.append(msg["frame"])
        self.handler(server, msg)


@pytest.fixture(scope="module", autouse=True)
def setup_peripheral_server():
    yield from SetupPeripheralServer.setup_peripheral_server()


@pytest.fixture(scope="module", autouse=True)
def setup_ethernet_virt_hub():
    # Choose an unused id.
    unique_msg_id = N_IIDS
    assert unique_msg_id not in range(N_IIDS)
    ethserver = HostEthernetServer("eth0", True, unique_msg_id)
    ioserver = IOServer(PS_TX_PORT, PS_RX_PORT)
    hub = ViruatalEthHub((ethserver, ioserver,))
    handled_ethernet_server_frames = HandledEthernetFrames(ethserver)
    ethserver.handler = handled_ethernet_server_frames.mock_handler
    ethserver.start()
    ioserver.start()
    time.sleep(1)
    # A hub with multiple IO servers may be set up, but it's unclear how these
    # servers communicate with the peripheral models, given that the peripheral
    # server is a global object, so it can communicate with just a single IO server.
    interrupter = SendInterrupt(ioserver)
    yield hub, interrupter, handled_ethernet_server_frames
    hub.shutdown()
    join_timeout(ioserver)
    ioserver.rx_socket.close()
    ioserver.tx_socket.close()
    join_timeout(ethserver)
    ethserver.host_socket.close()


def test_trigger_interrupt(setup_ethernet_virt_hub):
    (
        hub,
        interrupter,
        handled_ethernet_server_frames,
    ) = setup_ethernet_virt_hub
    n_interrupts = 2
    for i in range(n_interrupts):
        SetupPeripheralServer.qemu.irq_set_qmp.reset_mock()
        interrupter.trigger_interrupt(i)
        time.sleep(0.1)
        SetupPeripheralServer.qemu.irq_set_qmp.assert_called_once_with(i)


def test_received_frame(setup_ethernet_virt_hub):

    n_msgs = 2

    def message(iid, msg_num):
        return EthernetMessage(
            interface_id=iid,
            frame=f"message #{msg_num} to/from iid {iid}".encode(),
        )

    msg_list = [
        message(iid, msg_num)
        for iid in range(N_IIDS)
        for msg_num in range(n_msgs)
    ]

    (
        hub,
        interrupter,
        handled_ethernet_server_frames,
    ) = setup_ethernet_virt_hub
    EthernetModel.frame_queues.clear()
    EthernetModel.frame_times.clear()
    sent_times = defaultdict(list)

    class ScapySentFrames:
        scapy_sendp = scapy.sendp

        def __init__(self):
            self.sent_frames = []

        def sendp(self, packet, **kwargs):
            ScapySentFrames.scapy_sendp(packet, **kwargs)
            self.sent_frames.append(packet.load)

    # Add delay between sending mesages, so that the reception timing
    # threshold values below make sense.
    intermsg_send_delay = 0.3
    scapy_sent_frames = ScapySentFrames()
    for msg in msg_list:
        # Capture frames sent by scapy.sendp within the hub.received_frame calls.
        with mock.patch.object(
            scapy,
            "sendp",
            lambda packet, **kwargs: scapy_sent_frames.sendp(packet, **kwargs),
        ):
            hub.received_frame(None, msg)
        sent_times[msg["interface_id"]].append(time.time())
        time.sleep(intermsg_send_delay)

    # The frames sent by hub.received_frame.
    sent_frames = [msg["frame"] for msg in msg_list]
    # The sent frames are forwarded by HostEthernetServer via scapy.sendp.
    time.sleep(0.1)
    assert scapy_sent_frames.sent_frames == sent_frames
    # The forwarded frames are handled by ethserver in the same order.
    class ContainsElementsInOrder:
        """
        Check if all the elements from list lst1 in the same order in list lst2, and
        if so, report the extra elements in lst2.
        """

        def __init__(self):
            self.extra_elements_if_check_ok = []

        def check(self, lst1, lst2):
            idx = 0
            ret = True
            for elem in lst1:
                try:
                    idx2 = lst2.index(elem, idx)
                    self.extra_elements_if_check_ok += lst2[idx:idx2]
                    idx = idx2 + 1
                except ValueError:
                    ret = False
                    self.extra_elements_if_check_ok = None
                    break
            if ret:
                self.extra_elements_if_check_ok += lst2[idx:]
            return ret

    contains_elements_in_order = ContainsElementsInOrder()
    wait_assert(
        lambda: assert_(
            contains_elements_in_order.check,
            (
                scapy_sent_frames.sent_frames,
                handled_ethernet_server_frames.handled_frames,
            ),
        )
    )
    # Actually, the only frames handled by ethserver should be scapy_sent_frames.sent_frames.
    try:
        assert len(contains_elements_in_order.extra_elements_if_check_ok) == 0
    except:
        # The server may also receive and handle unexpected background frames.
        pytest.xfail(
            f"ERROR: handle unexpected background frames: {contains_elements_in_order.extra_elements}",
        )

    ## Check that the messages are received completely and in the expected order.
    sent_iids = set([msg["interface_id"] for msg in msg_list])
    ethserver_iid = hub.ioservers[0].msg_id
    assert set(EthernetModel.frame_queues.keys()) == sent_iids.union(
        {ethserver_iid}
    )
    # Check directly sent frames.
    for iid in sent_iids:
        assert list(EthernetModel.frame_queues[iid]) == [
            msg["frame"] for msg in msg_list if msg["interface_id"] == iid
        ]
    # Check forwarded frame copies.
    assert list(EthernetModel.frame_queues[ethserver_iid]) == sent_frames

    ## Check the timing.
    assert set(EthernetModel.frame_times.keys()) == sent_iids.union(
        {ethserver_iid}
    )
    # Received frames are separated by intermsg_send_delay.
    for iid in EthernetModel.frame_times:
        times_list = list(EthernetModel.frame_times[iid])
        for idx, ts_received in enumerate(times_list[:-1]):
            assert times_list[idx + 1] - ts_received > intermsg_send_delay

    # Directly sent frames are received within a specified delay.
    receive_delay_threshold = 0.1
    for iid in sent_iids:
        assert len(EthernetModel.frame_times[iid]) == len(sent_times[iid])
        for (ts_received, ts_sent) in zip(
            list(EthernetModel.frame_times[iid]), sent_times[iid]
        ):
            assert ts_received < ts_sent + receive_delay_threshold
            try:
                assert ts_received > ts_sent
            except:
                assert ts_received > ts_sent - 0.05
                pytest.xfail("time.time is not always monotonic")

    # Forwarded frame copies are received at about the same time as the
    # originals.  Note that, for the checks to make sense, intermsg_send_delay
    # is set sufficiently larger than simultaneous_threshold.
    simultaneous_threshold = 0.14
    assert intermsg_send_delay > 2 * simultaneous_threshold
    assert len(EthernetModel.frame_times[ethserver_iid]) == N_IIDS * n_msgs
    for iid in range(N_IIDS):
        for msg_num in range(n_msgs):
            ts_received_orig = EthernetModel.frame_times[iid][msg_num]
            ts_received_fwd = EthernetModel.frame_times[ethserver_iid][
                iid * n_msgs + msg_num
            ]
            assert (
                abs(ts_received_orig - ts_received_fwd)
                < simultaneous_threshold
            )
            # In this implementation, the commented assert below is not
            # guarantied to hold, but it's probably ok.
            #
            # assert ts_received_fwd > ts_received_orig
