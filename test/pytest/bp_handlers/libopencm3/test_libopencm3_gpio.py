from unittest import mock

import pytest
from arm_helpers import set_arguments

from halucinator.bp_handlers.libopencm3.libopencm3_gpio import LIBOPENCM3_GPIO


@pytest.fixture
def qemu():
    mock_model = mock.Mock()
    return mock_model


@pytest.fixture
def gpio():
    mock_model = mock.Mock()
    return LIBOPENCM3_GPIO(mock_model)


class TestLIBOPENCM3_GPIO:
    def test_return_ok_just_returns_zero(self, gpio):
        # Associated HAL functions declaration
        # void
        # gpio_set_mode (
        #   uint32_t gpioport,
        #   uint8_t mode,
        #   uint8_t cnf,
        #   uint16_t gpios
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/4a378a729a9f9b7f24e527e74dd38b5ae3b9bc69/lib/stm32/f1/gpio.c#L95
        continue_, retval = gpio.return_ok(None, None)
        assert continue_
        assert retval == 0

    @pytest.mark.parametrize("port", [16, 32])
    @pytest.mark.parametrize("pin", list(range(16)))
    def test_clear_pins_sets_all_pins_to_zero(self, qemu, gpio, port, pin):
        # Associated HAL functions declaration
        # void
        # gpio_clear (
        #   uint32_t gpioport,
        #   uint16_t gpios
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/4a378a729a9f9b7f24e527e74dd38b5ae3b9bc69/lib/stm32/common/gpio_common_all.c#L55
        gpio.model.write_pin = mock.Mock()
        set_arguments(qemu, [port])
        continue_, ret_val = gpio.clear_pins(qemu, None)
        assert continue_
        assert ret_val == 0
        gpio.model.write_pin.assert_any_call(hex(port) + "_" + str(pin), 0)

    @pytest.mark.parametrize("port", [16, 32])
    @pytest.mark.parametrize("pin", list(range(16)))
    @pytest.mark.parametrize(
        "ret_vals",
        [
            [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
            [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
            [0, 1, 0, 1, 0, 1, 0, 0, 1, 1, 0, 1, 1, 0, 1, 0],
        ],
    )
    def test_read_pins_returns_zero_when_gpios_empty(
        self, qemu, gpio, port, pin, ret_vals
    ):
        # Associated HAL functions declaration
        # uint16_t
        # gpio_get (
        #   uint32_t gpioport,
        #   uint16_t gpios
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/4a378a729a9f9b7f24e527e74dd38b5ae3b9bc69/lib/stm32/common/gpio_common_all.c#L70
        gpio.model.read_pin = mock.Mock(side_effect=ret_vals)
        set_arguments(qemu, [port, 0])
        continue_, ret_val = gpio.read_pins(qemu, None)
        assert continue_
        assert ret_val == 0
        gpio.model.read_pin.assert_any_call(hex(port) + "_" + str(pin))

    @pytest.mark.parametrize("port", [16, 32])
    @pytest.mark.parametrize("pin", list(range(16)))
    @pytest.mark.parametrize(
        "ret_vals, pins",
        [
            ([1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1], 0xFFFF),
            ([0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], 0),
            ([0, 1, 0, 1, 0, 1, 0, 0, 1, 1, 0, 1, 1, 0, 1, 0], 0x5B2A),
        ],
    )
    def test_read_pins_returns_unchanged_values_when_all_gpios_set_to_one(
        self, qemu, gpio, port, pin, ret_vals, pins
    ):
        # Associated HAL functions declaration
        # uint16_t
        # gpio_get (
        #   uint32_t gpioport,
        #   uint16_t gpios
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/4a378a729a9f9b7f24e527e74dd38b5ae3b9bc69/lib/stm32/common/gpio_common_all.c#L70
        gpio.model.read_pin = mock.Mock(side_effect=ret_vals)
        set_arguments(qemu, [port, 0xFFFF])
        continue_, ret_val = gpio.read_pins(qemu, None)
        assert continue_
        assert ret_val == pins
        gpio.model.read_pin.assert_any_call(hex(port) + "_" + str(pin))

    @pytest.mark.parametrize("port", [16, 32])
    @pytest.mark.parametrize("pin", list(range(16)))
    @pytest.mark.parametrize(
        "gpios, ret_vals, pins",
        [
            (0xF0F1, [1, 1, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1, 1, 0, 0, 1], 0x9071),
            (0x1234, [0, 1, 0, 1, 1, 0, 1, 0, 1, 1, 1, 1, 0, 1, 1, 1], 0x0210),
        ],
    )
    def test_read_pins_returns_bitand_masked_by_gpios_values(
        self, qemu, gpio, port, pin, gpios, ret_vals, pins
    ):
        # Associated HAL function declaration
        # uint16_t
        # gpio_get (
        #   uint32_t gpioport,
        #   uint16_t gpios
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/4a378a729a9f9b7f24e527e74dd38b5ae3b9bc69/lib/stm32/common/gpio_common_all.c#L70
        gpio.model.read_pin = mock.Mock(side_effect=ret_vals)
        set_arguments(qemu, [port, gpios])
        continue_, ret_val = gpio.read_pins(qemu, None)
        assert continue_
        assert ret_val == pins
        gpio.model.read_pin.assert_any_call(hex(port) + "_" + str(pin))

    @pytest.mark.parametrize("port", [16, 32])
    @pytest.mark.parametrize("pin", list(range(16)))
    def test_write_pins_set_selected_pin_to_one(self, qemu, gpio, port, pin):
        # Associated HAL function declaration
        # void
        # gpio_set (
        #   uint32_t gpioport,
        #   uint16_t gpios
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/4a378a729a9f9b7f24e527e74dd38b5ae3b9bc69/lib/stm32/common/gpio_common_all.c#L40
        gpio.model.write_pin = mock.Mock()
        set_arguments(qemu, [port, 1 << pin])
        continue_, ret_val = gpio.write_pins(qemu, None)
        assert continue_
        assert ret_val == 0
        gpio.model.write_pin.assert_called_once_with(hex(port) + "_" + str(pin), 1)

    @pytest.mark.parametrize("port", [16, 32])
    @pytest.mark.parametrize(
        "gpios, pin",
        [
            (0x0003, 0),
            (0x0003, 1),
            (0xF000, 12),
            (0xF000, 13),
            (0xF000, 14),
            (0xF000, 15),
            (0x0900, 8),
            (0x0900, 11),
        ],
    )
    def test_write_pins_set_selected_pins_to_one(
        self, qemu, gpio, port, gpios, pin
    ):
        # Associated HAL function declaration
        # void
        # gpio_set (
        #   uint32_t gpioport,
        #   uint16_t gpios
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/4a378a729a9f9b7f24e527e74dd38b5ae3b9bc69/lib/stm32/common/gpio_common_all.c#L40
        gpio.model.write_pin = mock.Mock()
        set_arguments(qemu, [port, gpios])
        continue_, ret_val = gpio.write_pins(qemu, None)
        assert continue_
        assert ret_val == 0
        gpio.model.write_pin.assert_any_call(hex(port) + "_" + str(pin), 1)
