# Copyright 2022 GrammaTech Inc.
from __future__ import annotations

import logging
from typing import Type

from halucinator.bp_handlers.bp_handler import BPHandler  # type: ignore
from halucinator.bp_handlers.bp_handler import HandlerReturn, bp_handler
from halucinator.peripheral_models.spi import SPIPublisher  # type: ignore
from halucinator.qemu_targets.arm_qemu import ARMQemuTarget  # type: ignore

log = logging.getLogger(__name__)


class LIBOPENCM3_SPI(BPHandler):
    def __init__(self, impl: Type[SPIPublisher] = SPIPublisher) -> None:
        self.model: Type[SPIPublisher] = impl

    @bp_handler(["spi_init_master"])
    def hal_init(self, qemu: ARMQemuTarget, bp_addr: int) -> HandlerReturn:
        log.info("spi_init_master called")
        return True, 0

    @bp_handler(
        [
            "spi_reset",
            "spi_enable",
            "spi_enable_software_slave_management",
            "spi_set_nss_high",
            "spi_enable_tx_dma",
            "spi_disable_tx_dma",
            "spi_enable_rx_dma",
            "spi_disable_rx_dma",
        ]
    )
    def hal_ok(self, qemu: ARMQemuTarget, bp_addr: int) -> HandlerReturn:
        log.info("Dummy return zero called")
        return True, 0
