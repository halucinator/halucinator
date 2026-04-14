from unittest import mock

import pytest
from arm_helpers import set_arguments

from halucinator.bp_handlers.stm32f4.stm32f4_gpio import STM32F4GPIO


@pytest.fixture
def qemu():
    mock_model = mock.Mock()
    return mock_model


@pytest.fixture
def gpio():
    mock_model = mock.Mock()
    return STM32F4GPIO(mock_model)


class TestSTM32F4GPIO:
    def test_get_id_returns_combination_of_hex_port_and_pin(self, gpio):
        PORT = 32
        PIN = 100
        EXPECTED_ID = "0x20_100"
        ret_id = gpio.get_id(PORT, PIN)
        assert EXPECTED_ID == ret_id

    def test_gpio_init_just_returns_zero(self, gpio):
        continue_, ret_val = gpio.gpio_init(None, None)
        assert continue_
        assert ret_val == 0

    def test_gpio_deinit_just_returns_zero(self, gpio):
        continue_, ret_val = gpio.gpio_deinit(None, None)
        assert continue_
        assert ret_val == 0

    @pytest.mark.parametrize(
        "port,pin,unique_id",
        [
            (32, 100, "0x20_100"),
            (16, 5, "0x10_5"),
            (1, 7, "0x1_7"),
            (16, 5, "0x10_5"),
        ],
    )
    @pytest.mark.parametrize("value", [0, 1])
    def test_write_pin_writes_value_correctly(
        self, qemu, gpio, port, pin, unique_id, value
    ):
        # void
        # HAL_GPIO_WritePin (
        #   GPIO_TypeDef *GPIOx,
        #   uint16_t GPIO_Pin,
        #   GPIO_PinState PinState
        # )
        # The under test function's description can be found here -
        # https://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32F439xx_User_Manual/group__gpio__exported__functions__group2.html#gaf4b97bdf533a02f51ef696d43b6da5c4
        gpio.model.write_pin = mock.Mock()
        set_arguments(qemu, [port, pin, value])
        intercept, ret_val = gpio.write_pin(qemu, None)
        assert intercept
        assert ret_val is None
        gpio.model.write_pin.assert_called_with(unique_id, value)

    @pytest.mark.parametrize(
        "port,pin,unique_id",
        [(1, 7, "0x1_7"), (16, 5, "0x10_5"), (17, 47, "0x11_47")],
    )
    def test_toggle_pin_toggles_correct_pin(
        self, qemu, gpio, port, pin, unique_id
    ):
        # void
        # HAL_GPIO_TogglePin (
        #   GPIO_TypeDef *GPIOx,
        #   uint16_t GPIO_Pin
        # )
        # The under test function's description can be found here -
        # https://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32F439xx_User_Manual/group__gpio__exported__functions__group2.html#gaf5e0c89f752de5cdedcc30db068133f6
        gpio.model.toggle_pin = mock.Mock()
        set_arguments(qemu, [port, pin])
        intercept, ret_val = gpio.toggle_pin(qemu, None)
        assert intercept
        assert ret_val is None
        gpio.model.toggle_pin.assert_called_with(unique_id)

    @pytest.mark.parametrize(
        "port,pin,unique_id",
        [
            (1, 7, "0x1_7"),
            (16, 5, "0x10_5"),
            (18, 33, "0x12_33"),
            (32, 100, "0x20_100"),
        ],
    )
    @pytest.mark.parametrize("value", [0, 1])
    def test_read_pin_reads_pin_correctly(
        self, qemu, gpio, port, pin, unique_id, value
    ):
        # GPIO_PinState
        # HAL_GPIO_ReadPin (
        #   GPIO_TypeDef *GPIOx,
        #   uint16_t GPIO_Pin
        # )
        # The under test function's description can be found here -
        # https://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32F439xx_User_Manual/group__gpio__exported__functions__group2.html#gaf2b819ea6551319ddd5670db318d2e4e
        gpio.model.read_pin = mock.Mock()
        gpio.model.read_pin.return_value = value
        set_arguments(qemu, [port, pin])
        intercept, ret_val = gpio.read_pin(qemu, None)
        assert intercept
        assert ret_val == value
        gpio.model.read_pin.assert_called_with(unique_id)
