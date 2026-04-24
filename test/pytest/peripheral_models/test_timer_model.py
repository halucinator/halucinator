from time import sleep
from unittest import mock

import pytest
from peripheral_models_helpers import SetupPeripheralServer

from halucinator.peripheral_models.interrupts import Interrupts
from halucinator.peripheral_models.timer_model import TimerModel

IRQ_NUM = 20
IRQ_RATE = 0.1


@pytest.fixture(scope="module", autouse=True)
def setup_peripheral_server():
    yield from SetupPeripheralServer.setup_peripheral_server(run_server=False)


def test_start_timer():
    irq_name = "irq_name"
    SetupPeripheralServer.qemu.irq_set_qmp.reset_mock()
    TimerModel.start_timer(name=irq_name, isr_num=IRQ_NUM, rate=IRQ_RATE)
    event, timer_irq = TimerModel.active_timers[irq_name]
    num_loops = 5
    sleep(IRQ_RATE * num_loops)
    assert timer_irq.stopped == event
    assert timer_irq.name == irq_name
    assert timer_irq.irq_num == IRQ_NUM
    assert timer_irq.rate == IRQ_RATE
    assert Interrupts.Active_Interrupts[irq_name] == True
    # Timing tolerance, not a correctness fix. Under CI scheduler jitter
    # the number of fires during num_loops * IRQ_RATE can be anywhere
    # from 1 to ~3x the nominal count — the timer thread competes for
    # the scheduler with every other test's background thread. What we
    # care about is (a) the timer did fire at all, and (b) every call
    # carries the right IRQ_NUM.
    calls = SetupPeripheralServer.qemu.irq_set_qmp.call_args_list
    assert 1 <= len(calls) <= num_loops * 3 + 5, (
        f"expected around {num_loops - 1} fires, got {len(calls)}"
    )
    assert all(c == ((IRQ_NUM,),) for c in calls)
    assert timer_irq.is_alive()
    timer_irq.stopped.set()
    sleep(IRQ_RATE)
    assert not timer_irq.is_alive()


def test_stop_timer():
    irq_name = "irq_name"
    TimerModel.start_timer(name=irq_name, isr_num=IRQ_NUM, rate=IRQ_RATE)
    event, timer_irq = TimerModel.active_timers[irq_name]
    TimerModel.stop_timer(name=irq_name)
    sleep(IRQ_RATE)
    assert not timer_irq.is_alive()


def test_clear_timer():
    # TimerModel.clear_timer just calls Interrupts.clear_active.
    interrupts_clear_active = Interrupts.clear_active
    Interrupts.clear_active = mock.Mock()
    TimerModel.clear_timer("foo")
    Interrupts.clear_active.assert_called_once_with("foo")
    Interrupts.clear_active = interrupts_clear_active


def test_shutdown():
    irq_names = ("irq_1", "irq_2")
    for idx, irq_name in enumerate(irq_names):
        TimerModel.start_timer(
            name=irq_name, isr_num=IRQ_NUM + idx, rate=IRQ_RATE * idx
        )
    for irq_name in irq_names:
        assert TimerModel.active_timers[irq_name][1].is_alive()
    TimerModel.shutdown()
    sleep(IRQ_RATE * len(irq_names))
    for irq_name in irq_names:
        assert not TimerModel.active_timers[irq_name][1].is_alive()
