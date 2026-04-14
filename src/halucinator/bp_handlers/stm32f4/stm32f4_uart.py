# Copyright 2019 National Technology & Engineering Solutions of Sandia, LLC (NTESS).
# Under the terms of Contract DE-NA0003525 with NTESS, the U.S. Government retains
# certain rights in this software.
from __future__ import annotations

from os import sys, path
from typing import TYPE_CHECKING, Type

from ...peripheral_models.uart import UARTPublisher
from ..bp_handler import BPHandler, HandlerReturn, bp_handler
import logging

if TYPE_CHECKING:
    from halucinator.qemu_targets.hal_qemu import HALQemuTarget
log = logging.getLogger(__name__)

from ... import hal_log
hal_log = hal_log.getHalLogger()


class STM32F4UART(BPHandler):

    def __init__(self, impl: Type[UARTPublisher] = UARTPublisher) -> None:
        self.model = impl

    @bp_handler(['HAL_UART_Init'])
    def hal_ok(self, qemu: HALQemuTarget, bp_addr: int) -> HandlerReturn:
        log.info("Init Called")
        return True, 0

    @bp_handler(['HAL_UART_GetState'])
    def get_state(self, qemu: HALQemuTarget, bp_addr: int) -> HandlerReturn:
        log.info("Get State")
        return True, 0x20  # 0x20 READY

    @bp_handler(['HAL_UART_Transmit', 'HAL_UART_Transmit_IT', 'HAL_UART_Transmit_DMA'])
    def handle_tx(self, qemu: HALQemuTarget, bp_addr: int) -> HandlerReturn:
        '''
            Reads the frame out of the emulated device, returns it and an 
            id for the interface(id used if there are multiple ethernet devices)
        '''
        huart = qemu.regs.r0
        hw_addr = qemu.read_memory(huart, 4, 1)
        buf_addr = qemu.regs.r1
        buf_len = qemu.regs.r2
        data = qemu.read_memory(buf_addr, 1, buf_len, raw=True)
        hal_log.info("UART TX:%r" % data)
        self.model.write(hw_addr, data)
        return True, 0

    # HAL_StatusTypeDef HAL_UART_Receive_IT(UART_HandleTypeDef *huart, uint8_t *pData, uint16_t Size);
    # HAL_StatusTypeDef HAL_UART_Transmit_DMA(UART_HandleTypeDef *huart, uint8_t *pData, uint16_t Size);
    # HAL_StatusTypeDef HAL_UART_Receive_DMA(UART_HandleTypeDef *huart, uint8_t *pData, uint16_t Size);
    @bp_handler(['HAL_UART_Receive', 'HAL_UART_Receive_IT', 'HAL_UART_Receive_DMA'])
    def handle_rx(self, qemu: HALQemuTarget, bp_handler: int) -> HandlerReturn:
        huart = qemu.regs.r0
        hw_addr = qemu.read_memory(huart, 4, 1)
        size = qemu.regs.r2
        log.info("Waiting for data: %i" % size)
        data = self.model.read(hw_addr, size, block=True)
        hal_log.info("UART RX: %r" % data)

        qemu.write_memory(qemu.regs.r1, 1, data, size, raw=True)
        return True, 0
