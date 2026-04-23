# Copyright 2019 National Technology & Engineering Solutions of Sandia, LLC (NTESS).
# Under the terms of Contract DE-NA0003525 with NTESS, the U.S. Government retains
# certain rights in this software.
import logging
from typing import Type

from halucinator import hal_log
from halucinator.bp_handlers.bp_handler import (
    BPHandler,
    HandlerReturn,
    bp_handler,
)
from halucinator.peripheral_models.uart import UARTPublisher
from halucinator.qemu_targets.arm_qemu import ARMQemuTarget

# TODO: remove and replace with just logger, I assume
log = logging.getLogger(__name__)

logger = hal_log.getHalLogger()


class STM32F1UART(BPHandler):
    def __init__(self, impl: Type[UARTPublisher] = UARTPublisher) -> None:
        self.model = impl

    @bp_handler(["HAL_UART_Init"])
    def hal_ok(self, qemu: ARMQemuTarget, bp_addr: int) -> HandlerReturn:
        log.info("Init Called")
        return True, 0

    @bp_handler(["HAL_UART_GetState"])
    def get_state(self, qemu: ARMQemuTarget, bp_addr: int) -> HandlerReturn:
        log.info("Get State")
        return True, 0x20  # 0x20 READY

    @bp_handler(
        ["HAL_UART_Transmit", "HAL_UART_Transmit_IT", "HAL_UART_Transmit_DMA"]
    )
    def handle_tx(self, qemu: ARMQemuTarget, bp_addr: int) -> HandlerReturn:
        """
            Reads the frame out of the emulated device, returns it and an
            id for the interface(id used if there are multiple ethernet devices)
        """
        #print("stm32f3_uart.py:STM32F1UART.handle_tx()")
        huart = qemu.regs.r0
        hw_addr = qemu.read_memory_word(huart)
        buf_addr = qemu.regs.r1
        buf_len = qemu.regs.r2
        data = qemu.read_memory_bytes(buf_addr, buf_len)
        logger.info("UART TX:%r" % data)
        #print(f" - calling uart.write() with uart_id:{hw_addr}, buff_addr:{buf_addr}, buf_len:{buf_len}")
        self.model.write(hw_addr, data)
        return True, 0

    # HAL_StatusTypeDef HAL_UART_Receive_IT(UART_HandleTypeDef *huart, uint8_t *pData, uint16_t Size);
    # HAL_StatusTypeDef HAL_UART_Transmit_DMA(UART_HandleTypeDef *huart, uint8_t *pData, uint16_t Size);
    # HAL_StatusTypeDef HAL_UART_Receive_DMA(UART_HandleTypeDef *huart, uint8_t *pData, uint16_t Size);
    @bp_handler(
        ["HAL_UART_Receive", "HAL_UART_Receive_IT", "HAL_UART_Receive_DMA"]
    )
    def handle_rx(self, qemu: ARMQemuTarget, bp_handler: int) -> HandlerReturn:
        #print("stm32f1_uart.py:STM32F1UART.handle_rx()")
        huart = qemu.regs.r0
        hw_addr = qemu.read_memory_word(huart)
        size = qemu.regs.r2
        #log.info("Waiting for data: %i" % size)
        print(f" - UART RX (uart_id:{hex(hw_addr)}), waiting on read of {size} chars...")
        data = self.model.read(hw_addr, size, block=True)
        logger.info("UART RX: %r" % data)

        assert size == len(data)
        qemu.write_memory_bytes(qemu.regs.r1, data)
        return True, 0
