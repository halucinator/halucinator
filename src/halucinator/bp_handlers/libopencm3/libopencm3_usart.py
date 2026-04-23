# Copyright 2022 GrammaTech Inc.
from __future__ import annotations

import logging
from typing import Type

from halucinator.bp_handlers.bp_handler import BPHandler  # type: ignore
from halucinator.bp_handlers.bp_handler import HandlerReturn, bp_handler
from halucinator.peripheral_models.uart import UARTPublisher  # type: ignore
from halucinator.qemu_targets.arm_qemu import ARMQemuTarget  # type: ignore

log = logging.getLogger(__name__)


class LIBOPENCM3_USART(BPHandler):
    def __init__(self, impl: Type[UARTPublisher] = UARTPublisher) -> None:
        self.model: Type[UARTPublisher] = impl

    @bp_handler(
        [
            "usart_set_baudrate",
            "usart_set_databits",
            "usart_set_stopbits",
            "usart_set_parity",
            "usart_set_mode",
            "usart_set_flow_control",
            "usart_enable",
            "usart_wait_send_ready",
        ]
    )
    def return_ok(self, qemu: ARMQemuTarget, bp_addr: int) -> HandlerReturn:
        # Associated HAL functions declaration
        # void
        # usart_set_baudrate (
        #   uint32_t usart,
        #   uint32_t baud
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/4a378a729a9f9b7f24e527e74dd38b5ae3b9bc69/lib/stm32/common/usart_common_all.c#L49
        # void
        # usart_enable (
        #   uint32_t usart
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/4a378a729a9f9b7f24e527e74dd38b5ae3b9bc69/lib/stm32/common/usart_common_all.c#L180
        return True, 0

    @bp_handler(["usart_send_blocking", "usart_send"])
    def write_single(self, qemu: ARMQemuTarget, bp_addr: int) -> HandlerReturn:
        # param[in] usart unsigned 32 bit. USART block register address base usart_reg_base
        # param[in] data unsigned 16 bit.
        #
        # void usart_send(uint32_t usart, uint16_t data);
        # void void usart_send_blocking(uint32_t usart, uint16_t data);
        # -------------------------------------------------------------
        # Associated HAL functions declaration
        # void
        # usart_send_blocking (
        #   uint32_t usart,
        #   uint16_t data
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/4a378a729a9f9b7f24e527e74dd38b5ae3b9bc69/lib/stm32/common/usart_common_all.c#L210
        # void
        # usart_send (
        #   uint32_t usart,
        #   uint16_t data
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/4a378a729a9f9b7f24e527e74dd38b5ae3b9bc69/lib/stm32/common/usart_common_f124.c#L44
        usart_ptr = qemu.regs.r0
        hw_addr = qemu.read_memory_word(usart_ptr)
        data = qemu.regs.r1
        log.debug("Tx_data: %s" % chr(data))
        self.model.write(hw_addr, data.to_bytes(2, "big"))
        return True, 0

    @bp_handler(["usart_recv"])
    def read_single(self, qemu: ARMQemuTarget, bp_addr: int) -> HandlerReturn:
        # param[in] usart unsigned 32 bit. USART block register address base usart_reg_base
        # returns unsigned 16 bit data word.
        #
        # uint16_t usart_recv(uint32_t usart);
        # -------------------------------------------------------------
        # Associated HAL function declaration
        # uint16_t
        # usart_recv (
        #   uint32_t usart
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/4a378a729a9f9b7f24e527e74dd38b5ae3b9bc69/lib/stm32/common/usart_common_f124.c#L61
        usart_ptr = qemu.regs.r0
        hw_addr = qemu.read_memory_word(usart_ptr)
        ret = self.model.read(hw_addr, 1, block=True)
        log.debug("Got Char: %s" % ret.hex())
        return True, int.from_bytes(ret, "big")
