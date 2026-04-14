import signal
from io import StringIO
from multiprocessing import Process
from os import kill
from time import sleep
from unittest import mock

import pytest
from delayed_signal_handling import AtomicCallCount
from main_mock import MainMock
from peripheral_models_helpers import SetupPeripheralServer, join_timeout

from halucinator.external_devices.ethernet_wireless import main
from halucinator.external_devices.trigger_interrupt import SendInterrupt


class XfailException(Exception):
    """
    Custom exception indicating xfailure.
    """

    pass


@pytest.fixture(scope="module", autouse=True)
def setup_peripheral_server():
    yield from SetupPeripheralServer.setup_peripheral_server()


@pytest.mark.xfail(
    raises=XfailException,
    reason="""need to wait for the server initialization to complete
            before starting the loop. Otherwise, initial inputs may
            remain unprocessed.""",
)
def test_main_interrupt():
    inputs = [str(intr) for intr in range(100)]
    counted_method = AtomicCallCount(SendInterrupt.trigger_interrupt, 0.1)
    main_mock = MainMock(
        main, ["-i", "eth0"], StringIO("\n".join(inputs)), counted_method
    )
    proc = Process(target=main_mock.main_mock)
    SetupPeripheralServer.qemu.trigger_interrupt.reset_mock()
    proc.start()
    # Let ethernet_wireless.main input loop get inputs from 'inputs' for some time.
    loop_time_sec = 1
    # In order to cover KeyboardInterrupt, we need to not run out of inputs
    # prior to triggering SIGINT. Otherwise, EOFError would be triggered first.
    num_inputs_before_sigint_upper_bound = (
        loop_time_sec / counted_method.wait_sec
    )
    assert len(inputs) > num_inputs_before_sigint_upper_bound
    sleep(loop_time_sec)
    kill(proc.pid, signal.SIGINT)
    join_timeout(proc)
    num_missed_inputs = (
        counted_method.num_calls
        - SetupPeripheralServer.qemu.trigger_interrupt.call_count
    )

    assert SetupPeripheralServer.qemu.trigger_interrupt.call_args_list == [
        mock.call(intr)
        for intr in range(num_missed_inputs, counted_method.num_calls)
    ]
    # Xfail the test until it's fixed in the tested code.
    if num_missed_inputs:
        raise XfailException


def test_main_num_ports():
    # Wait after starting session-level peripheral server
    counted_method = AtomicCallCount(SendInterrupt.trigger_interrupt, 0.1)
    main_mock = MainMock(main, ["-t", ""], StringIO(""), counted_method)
    proc = Process(target=main_mock.main_mock)
    SetupPeripheralServer.qemu.trigger_interrupt.reset_mock()
    proc.start()
    join_timeout(proc)
    assert main_mock.quit_flag
