# Copyright 2022 GrammaTech Inc.
from __future__ import annotations

import logging
from typing import Type

from halucinator.bp_handlers.bp_handler import BPHandler  # type: ignore
from halucinator.bp_handlers.bp_handler import HandlerReturn, bp_handler
from halucinator.peripheral_models.timer_model import (
    TimerModel,
)  # type: ignore
from halucinator.qemu_targets.arm_qemu import ARMQemuTarget  # type: ignore

log = logging.getLogger(__name__)


class LIBOPENCM3_RCC(BPHandler):
    def __init__(self, impl: Type[TimerModel] = TimerModel) -> None:
        self.model: Type[TimerModel] = impl

    @bp_handler(["rcc_osc_on"])
    def hal_osc_on(self, qemu: ARMQemuTarget, bp_addr: int) -> HandlerReturn:
        # Associated HAL function declaration
        # void
        # rcc_osc_on (
        #   enum rcc_osc osc
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/lib/stm32/f0/rcc.c#L244
        osc = qemu.regs.r0
        log.info("Oscillator %i enabled and on" % osc)
        return True, 0

    @bp_handler(["rcc_is_osc_ready"])
    def hal_osc_ready(
        self, qemu: ARMQemuTarget, bp_addr: int
    ) -> HandlerReturn:
        # Associated HAL function declaration
        # bool
        # rcc_is_osc_ready (
        #   enum rcc_osc osc
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/lib/stm32/f0/rcc.c#L206
        osc = qemu.regs.r0
        log.info("Oscillator %i is ready" % osc)
        # Virtual oscillator is always ready.
        return True, True

    @bp_handler(
        [
            "rcc_set_sysclk_source",
            "rcc_set_pll_multiplication_factor",
            "rcc_set_pll_source",
            "rcc_set_adcpre",
            "rcc_set_ppre2",
            "rcc_set_ppre1",
            "rcc_set_hpre",
            "rcc_clock_setup_in_hsi_out_48mhz",
            "rcc_periph_clock_enable",
            "rcc_periph_reset_pulse",
        ]
    )
    def hal_ok(self, qemu: ARMQemuTarget, bp_addr: int) -> HandlerReturn:
        # Associated HAL functions declaration can be found here -
        # https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/lib/stm32/f0/rcc.c
        # https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/lib/stm32/f1/rcc.c
        log.info("RCC Dummy return zero called")
        return True, 0
