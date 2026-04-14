from unittest import mock

import IPython
import pytest
from arm_helpers import create_read_memory_fake, set_arguments

from halucinator.bp_handlers.atmel_asf_v3.timers import Timers

IDX = 2
WORD_SIZE = 4
ONE_WORD = 1
TC_INSTANCES_PTR = 0x000024E0
TC_INSTANCES = 0x200021AC
TC_INSTANCE = 0x10203040
TC_INSTANCES_OFFSET = 0x200021B4
assert TC_INSTANCES_OFFSET == TC_INSTANCES + IDX * WORD_SIZE
HW_ADR = 0x48402080
AT_TC_INSTANCES_PTR = 0x1820

MEMORY = {
    # Parameters and return value structure for qemu memory_read function
    # <address>: [<return value>, <expected wordsize>, <expected number of words>, <raw - True or False>]
    # Missed values will be replaced with default values
    TC_INSTANCES_OFFSET: [TC_INSTANCE, WORD_SIZE],
    TC_INSTANCE: [HW_ADR, WORD_SIZE, ONE_WORD],
    TC_INSTANCES_PTR: [AT_TC_INSTANCES_PTR, WORD_SIZE, ONE_WORD],
}


@pytest.fixture
def sd_qemu_mock(qemu_mock):
    qemu_mock.read_memory.side_effect = create_read_memory_fake(MEMORY)
    return qemu_mock


@pytest.fixture
def timers():
    mock_model = mock.Mock()
    return Timers(mock_model)


class TestTimers:
    def test_enable_starts_all_timers(self, timers, sd_qemu_mock):
        # Associated HAL fuction declaration
        # enum status_code
        # tc_init (
        #   struct tc_module *const module_inst,
        #   Tc *const hw,
        #   const struct tc_config *const config
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/sam0.applications.xosc32k_fail_detector.saml21_xplained_pro/html/group__asfdoc__sam0__tc__group.html#ga98c7f5c97436c2f6cff87a0261597337
        TIMER1 = "Timer0"
        TIMER2 = "Timer1"
        TIMER3 = "Timer3"
        TIMER4 = "Timer4"
        TIMER5 = "Timer5"
        RATE1 = 1
        RATE2 = 2
        RATE3 = 3
        RATE4 = 4
        RATE5 = 5
        timers.irq_rates = {
            TIMER1: RATE1,
            TIMER2: RATE2,
            TIMER3: RATE3,
            TIMER4: RATE4,
            TIMER5: RATE5,
        }  # In real use, .irq_rates would be set in register_handler, but we don't really want to call that here.

        # The enable() function ignores the arguments passed in for actual behavior, but does retrieve r0 and r1 for purposes of logging.
        # Pass dummy values for those arguments. Will need to set actual proper values if the implementation function's fidelity is improved.
        sd_qemu_mock.regs.r0 = 1
        sd_qemu_mock.regs.r1 = 2
        timers.model.start_timer = mock.Mock()
        continue_, ret_val = timers.enable(sd_qemu_mock, None)
        assert not continue_
        assert ret_val == None
        # The 0x20, 0x31, etc. are hard-coded in Timers.init (even though .irq_rates has to be set separately with keys that somewhat agree)"
        timers.model.start_timer.assert_any_call(TIMER1, 0x20, RATE1)
        timers.model.start_timer.assert_any_call(TIMER2, 0x21, RATE2)
        timers.model.start_timer.assert_any_call(TIMER3, 0x22, RATE3)
        timers.model.start_timer.assert_any_call(TIMER4, 0x23, RATE4)
        timers.model.start_timer.assert_any_call(TIMER5, 0x24, RATE5)

    def test_enabling_non_existing_timer_causes_exception(
        self, timers, sd_qemu_mock
    ):
        # Associated HAL fuction declaration
        # enum status_code
        # tc_init (
        #   struct tc_module *const module_inst,
        #   Tc *const hw,
        #   const struct tc_config *const config
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/sam0.applications.xosc32k_fail_detector.saml21_xplained_pro/html/group__asfdoc__sam0__tc__group.html#ga98c7f5c97436c2f6cff87a0261597337
        NON_EXISTING_TIMER = "Timer2"
        RATE = 100
        timers.irq_rates = {
            NON_EXISTING_TIMER: RATE,
        }
        set_arguments(
            sd_qemu_mock, [1, 2]
        )  # Assign something because it used in logs only
        timers.model.start_timer = mock.Mock()
        with pytest.raises(KeyError, match=NON_EXISTING_TIMER):
            continue_, ret_val = timers.enable(sd_qemu_mock, None)

    @mock.patch.object(IPython, "embed")
    def test_isr_handler_reads_qemu_memory_correctly(
        self, timers, sd_qemu_mock
    ):
        set_arguments(sd_qemu_mock, [IDX])
        sd_qemu_mock.regs.r4 = (
            5  # Assign something because it used in logs only
        )
        sd_qemu_mock.regs.pc = (
            0xDEED  # Assign something because it used in logs only
        )
        # There is a known pytest issue related to mocking objects. So we need to use a real object to bypass the issue.
        tmr = Timers()
        continue_, ret_val = tmr.isr_handler(sd_qemu_mock, None)
        assert not continue_
        assert ret_val == None
        IPython.embed.assert_called_once_with()

    def test_disable_stops_timer(self, timers):
        IRQ_NAME = "Timer1"
        timers.model.stop_timer = mock.Mock()
        timers.disable(IRQ_NAME)
        timers.model.stop_timer.assert_called_once_with(IRQ_NAME)

    @pytest.mark.xfail
    @pytest.mark.parametrize("address", [0x42002000, 0x42003FFF])
    def test_avatar_read_memory_in_specific_range_calls_returns_zero(
        self, timers, address
    ):
        SIZE = 0x2000
        ret = timers.read_memory(address, SIZE)
        assert ret == 0

    @pytest.mark.xfail
    def test_avatar_read_memory_in_specific_range_calls_hw_read_and_returns_0xff_when_offset_TC3_INT_Reg(
        self, timers
    ):
        ADDRESS = 0x42002C0E
        SIZE = 0x1000
        ret = timers.read_memory(ADDRESS, SIZE)
        assert ret == 0xFF

    @pytest.mark.parametrize(
        "address", [0x2000, 0x42001FFF, 0x42004000, 0x62002000]
    )
    def test_avatar_read_memory_out_of_specific_range_causes_exception(
        self, timers, address
    ):
        SIZE = 0x1
        with pytest.raises(Exception):
            ret = timers.read_memory(address, SIZE)
            assert ret == 0

    @pytest.mark.xfail
    @pytest.mark.parametrize("address", [0x42002000, 0x42003FFF])
    def test_avatar_write_memory_in_specific_range_calls_returns_True(
        self, timers, address
    ):
        SIZE = 0x2000
        VALUE = b"\0x11" * SIZE
        ret = timers.write_memory(address, SIZE, VALUE)
        assert ret

    @pytest.mark.parametrize(
        "address", [0x2000, 0x42001FFF, 0x42004000, 0x62002000]
    )
    def test_avatar_write_memory_out_of_specific_range_causes_exception(
        self, timers, address
    ):
        SIZE = 0x1
        VALUE = b"\0x22"
        with pytest.raises(Exception):
            ret = timers.write_memory(address, SIZE, VALUE)
            assert ret
