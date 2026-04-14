import operator
import os
import signal
import socket
import time
from ctypes import c_char_p
from multiprocessing import Manager, Process
from threading import Thread
from time import sleep
from unittest import mock

import pytest
import scapy.all as scapy
import zmq

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

import halucinator.external_devices.host_ethernet as HE
import halucinator.peripheral_models.peripheral_server as PS
from halucinator.peripheral_models.ethernet import (
    EthernetMessage,
    EthernetModel,
)


class ProcessStart:
    children = []
    orig_start = Process.start

    @staticmethod
    def start_log(self):
        ProcessStart.orig_start(self)
        ProcessStart.children.append(self)


class RxFromEmulator:
    rx_from_emulator = HE.rx_from_emulator

    def __init__(self):
        self.xfail_msg = Manager().Value(c_char_p, "")

    def mock(self, emu_rx_port, interface):
        try:
            RxFromEmulator.rx_from_emulator(emu_rx_port, interface)
        except TypeError as ex:
            # Even though we expect it to happen, no point in asserting it in a
            # subprocess.
            if str(ex) == "unicode not allowed, use setsockopt_string":
                self.xfail_msg.value = (
                    f"BUG #1: host_ethernet.rx_from_emulator: {ex}"
                )
            else:
                raise


def run_server_value():
    return HE.__run_server


class HostEthernetStart:
    def __init__(self):
        self.xfail_msg = Manager().Value(c_char_p, "")
        self.run_server = Manager().Value("i", True)
        self.rx_from_emulator = RxFromEmulator()

    def mock(self):
        with mock.patch(
            "multiprocessing.Process.start", ProcessStart.start_log
        ), mock.patch(
            "halucinator.external_devices.host_ethernet.rx_from_emulator",
            self.rx_from_emulator.mock,
        ):
            try:
                # Start the server with non-default ports, lest to break the
                # session-wide setup.
                assert not {PS_RX_PORT, PS_TX_PORT}.intersection({8888, 9999})
                HE.start("eth0", 8888, 9999)
            except AttributeError as ex:
                # Even though we expect it to happen, no point in asserting it in a
                # subprocess.
                if str(ex) == "'NoneType' object has no attribute 'join'":
                    self.xfail_msg.value = f"BUG #2: host_ethernet.start: {ex}"
                    self.run_server.value = run_server_value()
                    sleep(0.2)
                    for proc in ProcessStart.children:
                        proc.terminate()
                else:
                    raise


@pytest.fixture(scope="module", autouse=True)
def setup_peripheral_server():
    yield from SetupPeripheralServer.setup_peripheral_server()


@pytest.mark.parametrize(
    "failure_index", range(3),
)
def test_start_bugs(failure_index):
    xfail_msgs = []
    host_ethernet_start = HostEthernetStart()
    proc = Process(target=host_ethernet_start.mock)
    proc.start()
    try:
        # Wait till the host_ethernet.start begins running its loop, so proc
        # can handle KeyboardInterrupt.
        proc.join(0.2)
        assert proc.is_alive()
        # Assert the first xfailure.
        assert "BUG #1" in host_ethernet_start.rx_from_emulator.xfail_msg.value
        xfail_msgs.append(host_ethernet_start.rx_from_emulator.xfail_msg.value)
        # Raise KeyboardInterrupt in proc.
        os.kill(proc.pid, signal.SIGINT)
        sleep(0.01)
        # Assert the second xfailure.
        assert "BUG #2" in host_ethernet_start.xfail_msg.value
        xfail_msgs.append(host_ethernet_start.xfail_msg.value)
        # Assert that, in proc,  __run_server == False
        assert host_ethernet_start.run_server.value is False
        proc.join(0.1)
        # Assert the third xfailure.
        assert proc.is_alive()
        xfail_msgs.append(
            "BUG #3: host_ethernet.start: emu_rx_process and/or emu_tx_process "
            "do not terminate even though HE.__run_server is False."
        )
        # Xfail the test until it's fixed in the tested code.
        assert len(xfail_msgs) == 3
        assert failure_index < len(xfail_msgs)
        pytest.xfail(xfail_msgs[failure_index])
    finally:
        join_timeout(proc)


class RxFromEmulatorBugPatch(RxFromEmulator):
    decode_zmq_msg = PS.decode_zmq_msg

    def __init__(self):
        super().__init__()
        self.received_data = []

    def decode_zmq_msg_mock(self, msg):
        topic, data = RxFromEmulatorBugPatch.decode_zmq_msg(msg)
        self.received_data.append(data)
        return (topic, data)

    def mock(self, emu_rx_port, interface):
        with mock.patch(
            "zmq.Socket.setsockopt", zmq.Socket.setsockopt_string
        ), mock.patch(
            "halucinator.external_devices.host_ethernet.decode_zmq_msg",
            self.decode_zmq_msg_mock,
        ):
            super().mock(emu_rx_port, interface)


N_IIDS = 2
N_FRAMES = 2


def test_rx_from_emulator():
    def frame_to_iid(iid, frame_num):
        return f"frame #{frame_num} for iid #{iid}".encode()

    # Patch the rx_from_emulator setsockopt call bug.
    rx_from_emulator = RxFromEmulatorBugPatch()
    # Start rx_from_emulator thread.
    HE.__run_server = True
    rx_from_emulator_thread = Thread(
        target=rx_from_emulator.mock, args=(PS_TX_PORT, "eth0",)
    )
    rx_from_emulator_thread.start()
    # Let rx_from_emulator start its listening loop.
    sleep(0.8)
    # Send messages from EthernetModel.
    msgs_to_send = [
        EthernetMessage(interface_id=iid, frame=frame_to_iid(iid, frame_num))
        for iid in range(N_IIDS)
        for frame_num in range(N_FRAMES)
    ]
    for msg in msgs_to_send:
        EthernetModel.tx_frame(msg["interface_id"], msg["frame"])
    # Check that all the messages are received.
    wait_assert(
        lambda: assert_(
            operator.eq, (rx_from_emulator.received_data, msgs_to_send)
        )
    )
    HE.__run_server = False
    EthernetModel.tx_frame("an_interface_id", b"a_terminating_frame")
    join_timeout(rx_from_emulator_thread)


@pytest.mark.skip
def test_rx_from_host():
    # Set up HE.__host_socket as in HE.start
    os.system("ip link set %s promisc on" % "eth0")  # Set to permisucous
    ETH_P_ALL = 3
    HE.__host_socket = socket.socket(
        socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ALL)
    )
    HE.__host_socket.bind(("eth0", 0))

    def frame_from_host(frame_num):
        return f"frame #{frame_num} from host".encode()

    # Start device listening thread.
    HE.__run_server = True
    IID = 1073905664
    rx_from_host_thread = Thread(
        target=HE.rx_from_host, args=(PS_RX_PORT, IID,)
    )
    rx_from_host_thread.start()
    sleep(0.2)

    # Send frames.
    EthernetModel.frame_queues.clear()
    EthernetModel.frame_times.clear()

    sent_frames = [frame_from_host(frame_num) for frame_num in range(N_FRAMES)]
    sent_times = []
    for frame in sent_frames:
        scapy.sendp(scapy.Raw(frame))
        sent_times.append(time.time())
    # Let it work through.
    wait_assert(
        lambda: assert_(
            operator.eq, (list(EthernetModel.frame_queues.keys()), [IID])
        )
    )
    wait_assert(
        lambda: assert_(
            operator.eq, (list(EthernetModel.frame_queues[IID]), sent_frames)
        )
    )
    assert list(EthernetModel.frame_times.keys()) == [IID]
    assert len(EthernetModel.frame_times[IID]) == len(sent_times)
    try:
        for (ts_recv, ts_sent) in zip(
            list(EthernetModel.frame_times[IID]), sent_times
        ):
            assert ts_recv - ts_sent < 0.3
            # Got assert failure 1638828385.2601104 > 1638828385.2765427
            # for the exact time comparison below. It must be time.time() reporting fluctuations.
            try:
                assert ts_recv > ts_sent
            except AssertionError:
                assert ts_recv > ts_sent - 0.05
                pytest.xfail("time.time is not always monotonic.")
    finally:
        HE.__run_server = False
        scapy.sendp(scapy.Raw(b"a_terminating_frame"))
        join_timeout(rx_from_host_thread)
