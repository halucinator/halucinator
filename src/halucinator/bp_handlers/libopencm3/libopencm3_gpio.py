# Copyright 2022 GrammaTech Inc.
from __future__ import annotations

from typing import Type

from halucinator.bp_handlers.bp_handler import BPHandler  # type: ignore
from halucinator.bp_handlers.bp_handler import HandlerReturn, bp_handler
from halucinator.peripheral_models.gpio import GPIO  # type: ignore
from halucinator.qemu_targets.arm_qemu import ARMQemuTarget  # type: ignore

NUMBER_OF_GPIOS = 16


class LIBOPENCM3_GPIO(BPHandler):
    def __init__(self, model: Type[GPIO] = GPIO) -> None:
        self.model: Type[GPIO] = model

    def get_id(self, port: int, pin: int) -> str:
        """
            Creates a unique id for the port and pin
        """
        return hex(port) + "_" + str(pin)

    @bp_handler(["gpio_set_mode"])
    def return_ok(self, qemu: ARMQemuTarget, bp_addr: int) -> HandlerReturn:
        # Associated HAL function declaration
        # void
        # gpio_set_mode (
        #   uint32_t gpioport,
        #   uint8_t mode,
        #   uint8_t cnf,
        #   uint16_t gpios
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/4a378a729a9f9b7f24e527e74dd38b5ae3b9bc69/lib/stm32/f1/gpio.c#L95
        return True, 0

    @bp_handler(["gpio_clear"])
    def clear_pins(self, qemu: ARMQemuTarget, bp_addr: int) -> HandlerReturn:
        """
            Clear all GPIO pins
        """
        # Associated HAL function declaration
        # void
        # gpio_clear (
        #   uint32_t gpioport,
        #   uint16_t gpios
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/4a378a729a9f9b7f24e527e74dd38b5ae3b9bc69/lib/stm32/common/gpio_common_all.c#L55
        port = qemu.regs.r0
        for pin in range(NUMBER_OF_GPIOS):
            gpio_id = self.get_id(port, pin)
            self.model.write_pin(gpio_id, 0)

        intercept = True  # Don't execute real function
        ret_val = 0
        return intercept, ret_val

    @bp_handler(["gpio_get"])
    def read_pins(self, qemu: ARMQemuTarget, bp_addr: int) -> HandlerReturn:
        """
            Read provided GPIO pins
        """
        # Associated HAL function declaration
        # uint16_t
        # gpio_get (
        #   uint32_t gpioport,
        #   uint16_t gpios
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/4a378a729a9f9b7f24e527e74dd38b5ae3b9bc69/lib/stm32/common/gpio_common_all.c#L70
        port = qemu.regs.r0
        gpios = qemu.regs.r1
        pins = 0
        for pin in range(NUMBER_OF_GPIOS):
            gpio_id = self.get_id(port, pin)
            value = self.model.read_pin(gpio_id)
            pins += value << pin

        intercept = True  # Don't execute real function
        ret_val = pins & gpios
        return intercept, ret_val

    @bp_handler(["gpio_set"])
    def write_pins(self, qemu: ARMQemuTarget, bp_addr: int) -> HandlerReturn:
        """
            Set provided GPIO pins
        """
        # Associated HAL function declaration
        # void
        # gpio_set (
        #   uint32_t gpioport,
        #   uint16_t gpios
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/4a378a729a9f9b7f24e527e74dd38b5ae3b9bc69/lib/stm32/common/gpio_common_all.c#L40
        port = qemu.regs.r0
        gpios = qemu.regs.r1
        for pin in range(NUMBER_OF_GPIOS):

            value = gpios & 1
            if value == 1:
                gpio_id = self.get_id(port, pin)
                self.model.write_pin(gpio_id, 1)
            gpios >>= 1

        intercept = True  # Don't execute real function
        ret_val = 0
        return intercept, ret_val
