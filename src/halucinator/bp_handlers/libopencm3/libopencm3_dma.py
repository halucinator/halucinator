# Copyright 2022 GrammaTech Inc.
from __future__ import annotations

import logging
from typing import Type

from halucinator.bp_handlers.bp_handler import BPHandler  # type: ignore
from halucinator.bp_handlers.bp_handler import HandlerReturn, bp_handler
from halucinator.peripheral_models.spi import SPIPublisher  # type: ignore
from halucinator.qemu_targets.arm_qemu import ARMQemuTarget  # type: ignore

log = logging.getLogger(__name__)


class LIBOPENCM3_DMA(BPHandler):
    def __init__(self, impl: Type[SPIPublisher] = SPIPublisher) -> None:
        self.model: Type[SPIPublisher] = impl

    @bp_handler(
        [
            "dma_channel_reset",
            "dma_set_priority",
            "dma_set_memory_size",
            "dma_set_peripheral_size",
            "dma_enable_memory_increment_mode",
            "dma_set_read_from_peripheral",
            "dma_set_read_from_memory",
            "dma_enable_transfer_complete_interrupt",
            "dma_disable_transfer_complete_interrupt",
            "dma_enable_channel",
            "dma_disable_channel",
            "dma_set_peripheral_address",
            "dma_set_memory_address",
            "dma_set_number_of_data",
        ]
    )
    def hal_ok(self, qemu: ARMQemuTarget, bp_addr: int) -> HandlerReturn:
        log.info("Dummy return zero called")
        return True, 0
