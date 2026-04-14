# Copyright 2022 GrammaTech Inc.
import types
from unittest import mock

import pytest
from arm_helpers import set_arguments

from halucinator.bp_handlers.libopencm3.libopencm3_flash import (
    LIBOPENCM3_Flash,
)
from halucinator.qemu_targets.arm_qemu import ARMQemuTarget


@pytest.fixture
def flash():
    me = LIBOPENCM3_Flash()
    # Registering the handler is needed to establish the size of
    # the Flash memory we are emulating. The clear routines we
    # harness need a registered address.
    qemu_mock = mock.Mock()
    me.register_handler(qemu_mock, 0x8000010, "flash_clear_status_flags")
    me.register_handler(qemu_mock, 0x8000000, "flash_get_status_flags")
    assert len(me.addr_names) >= 2
    return me


# Customized version of qemu_mock that also captures write_memory itself.
# If we do this with qemu_mock, it messes up other unit tests.
@pytest.fixture
def lcm3_flash_qemu_mock(qemu_mock):
    qemu_mock.write_memory = types.MethodType(
        ARMQemuTarget.write_memory, qemu_mock
    )

    return qemu_mock


class TestLIBOPENCM3_Flash:
    def test_hal_ok_just_returns_zero(self, flash):
        # Associated HAL function declaration
        # void
        # flash_wait_for_last_operation (
        #   void
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/include/libopencm3/stm32/common/flash_common_f.h#L42
        continue_, retval = flash.hal_ok(None, None)
        assert continue_
        assert retval == 0

    @pytest.mark.parametrize("ws", [1, 5, 10, 100])
    def test_hal_flash_set_ws_just_returns_zero(self, qemu_mock, flash, ws):
        # Associated HAL function declaration
        # void
        # flash_set_ws (
        #   uint32_t ws
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/include/libopencm3/stm32/common/flash_common_all.h#L55
        set_arguments(qemu_mock, [ws])
        continue_, retval = flash.hal_flash_set_ws(qemu_mock, None)
        assert continue_
        assert retval == 0

    def test_hal_flash_lock_just_returns_zero(self, flash):
        # Associated HAL function declaration
        # void
        # flash_lock (
        #   void
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/include/libopencm3/stm32/common/flash_common_all.h#L60
        continue_, retval = flash.hal_flash_lock(None, None)
        assert continue_
        assert retval == 0

    def test_hal_flash_unlock_just_returns_zero(self, flash):
        # Associated HAL function declaration
        # void
        # flash_unlock (
        #   void
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/include/libopencm3/stm32/common/flash_common_all.h#L66
        continue_, retval = flash.hal_flash_unlock(None, None)
        assert continue_
        assert retval == 0

    def test_hal_flags_change_as_expected(self, qemu_mock, flash):
        # The flash_get_status_flags, flash_clear_flags, and flash_write_word
        # are not independent of each other so they must be tested as a group.
        continue_, retval = flash.hal_flash_clear_flags(None, 0x8000010)
        assert continue_
        assert retval == 0
        # After being cleared, flags are zero
        continue_, retval = flash.hal_flash_get_status_flags(None, None)
        assert continue_
        assert retval == 0
        set_arguments(qemu_mock, [0x8001000, 0x4321])
        qemu_mock.write_memory_word = mock.Mock()
        continue_, retval = flash.hal_flash_program_word(qemu_mock, None)
        assert continue_
        assert retval == 0
        # After the write, the EOP flag (0x20) should be set.
        continue_, retval = flash.hal_flash_get_status_flags(None, None)
        assert continue_
        assert retval == 0x20
        # Now we should be able to clear it again.
        continue_, retval = flash.hal_flash_clear_flags(None, 0x8000010)
        assert continue_
        assert retval == 0
        continue_, retval = flash.hal_flash_get_status_flags(None, None)
        assert continue_
        assert retval == 0

    @pytest.mark.parametrize(
        "address", [0x8001000, 0x8002200, 0x8004504, 0x8003418]
    )
    @pytest.mark.parametrize(
        "data", [0xAABBCCDD, 0xDEADBEEF, 0xF00DDEED, 0xFFEEDDCC]
    )
    def test_hal_flash_program_word_writes_word_correctly(
        self, qemu_mock, flash, address, data
    ):
        # Associated HAL function declaration
        # void
        # flash_program_word (
        #   uint32_t address,
        #   uint32_t data
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/lib/stm32/common/flash_common_f01.c#L82
        set_arguments(qemu_mock, [address, data])
        qemu_mock.write_memory_word = mock.Mock()
        continue_, retval = flash.hal_flash_program_word(qemu_mock, None)
        assert continue_
        assert retval == 0
        qemu_mock.write_memory_word.assert_called_once_with(address, data)

    def test_hal_flash_erases_page_correctly(
        self, lcm3_flash_qemu_mock, flash
    ):
        # Associated HAL function declaration
        # void flash_erase_page(uint32_t page_address)
        #
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/lib/stm32/f1/flash.c#L252
        address = 0x8001234
        set_arguments(lcm3_flash_qemu_mock, [address])
        lcm3_flash_qemu_mock.write_memory = mock.Mock()
        continue_, retval = flash.hal_flash_erase_page(
            lcm3_flash_qemu_mock, None
        )
        assert continue_
        assert retval == 0
        pgaddr = address & (~1023)
        lcm3_flash_qemu_mock.write_memory.assert_called_once_with(
            pgaddr, 1, b"\xff" * 1024, 1024, raw=True
        )

    @pytest.mark.parametrize("address", [0x7001234, 0x900000])
    def test_hal_flash_bounds_check(
        self, lcm3_flash_qemu_mock, flash, address
    ):
        # These check hal_flash_write_word and hal_flash_erase_page
        # bounds checking (for better coverage).

        set_arguments(lcm3_flash_qemu_mock, [address, 0xBEEF])
        lcm3_flash_qemu_mock.write_memory_word = mock.Mock()
        continue_, retval = flash.hal_flash_program_word(
            lcm3_flash_qemu_mock, None
        )
        assert not continue_
        assert retval == 0

        set_arguments(lcm3_flash_qemu_mock, [address])
        lcm3_flash_qemu_mock.write_memory = mock.Mock()
        continue_, retval = flash.hal_flash_erase_page(
            lcm3_flash_qemu_mock, None
        )
        assert not continue_
        assert retval == 0
