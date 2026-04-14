# Copyright 2022 GrammaTech Inc.
from __future__ import annotations

import logging
import time
from typing import Dict

from halucinator.bp_handlers.bp_handler import BPHandler  # type: ignore
from halucinator.bp_handlers.bp_handler import HandlerReturn, bp_handler
from halucinator.qemu_targets.arm_qemu import ARMQemuTarget  # type: ignore

log = logging.getLogger(__name__)


class LIBOPENCM3_Timer(BPHandler):
    def __init__(self) -> None:
        self.start_time: Dict[int, float] = {}
        self.clock_div: Dict[int, int] = {}
        self.period: Dict[int, int] = {}

    @bp_handler(["timer_set_mode"])
    def hal_timer_set_mode(
        self, qemu: ARMQemuTarget, bp_addr: int
    ) -> HandlerReturn:
        # Associated HAL function declaration
        # void
        # timer_set_clock_division (
        #   uint32_t timer_peripheral,
        #   uint32_t clock_div,
        #   uint32_t alignment,
        #   uint32_t direction
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/lib/stm32/common/timer_common_all.c#L237
        timer_id = qemu.regs.r0
        div = qemu.regs.r1
        self.start_time[timer_id] = 0.0
        self.clock_div[timer_id] = div
        self.period[timer_id] = 1
        log.info("Timer %i set" % timer_id)
        return True, 0

    @bp_handler(["timer_set_clock_division"])
    def hal_timer_set_clock_division(
        self, qemu: ARMQemuTarget, bp_addr: int
    ) -> HandlerReturn:
        # Associated HAL function declaration
        # void
        # timer_set_clock_division (
        #   uint32_t timer_peripheral,
        #   uint32_t clock_div
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/lib/stm32/common/timer_common_all.c#L263
        timer_id = qemu.regs.r0
        div = qemu.regs.r1
        self.clock_div[timer_id] = div
        log.info("Timer %i divider set to %i" % (timer_id, div))
        return True, 0

    @bp_handler(["timer_set_prescaler"])
    def hal_timer_set_prescaler(
        self, qemu: ARMQemuTarget, bp_addr: int
    ) -> HandlerReturn:
        # Associated HAL function declaration
        # void
        # timer_set_prescaler (
        #   uint32_t timer_peripheral,
        #   uint32_t value
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/lib/stm32/common/timer_common_all.c#L650
        timer_id = qemu.regs.r0
        prescale = qemu.regs.r1
        # Opposing to the clock division prescaler value increased by 1 when used for frequency division
        self.clock_div[timer_id] = prescale + 1
        log.info("Timer %i prescale set to %i" % (timer_id, prescale))
        return True, 0

    @bp_handler(["timer_enable_counter"])
    def hal_timer_enable_counter(
        self, qemu: ARMQemuTarget, bp_addr: int
    ) -> HandlerReturn:
        # Associated HAL function declaration
        # void
        # timer_enable_counter (
        #   uint32_t timer_peripheral
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/lib/stm32/common/timer_common_all.c#L435
        timer_id = qemu.regs.r0
        # Set default division and period if they do not exist
        if timer_id not in self.clock_div:
            self.clock_div[timer_id] = 1
        if timer_id not in self.period:
            self.period[timer_id] = 1
        self.start_time[timer_id] = time.time()
        log.info("Timer %i started" % timer_id)
        return True, 0

    @bp_handler(["timer_disable_counter"])
    def hal_timer_disable_counter(
        self, qemu: ARMQemuTarget, bp_addr: int
    ) -> HandlerReturn:
        # Associated HAL function declaration
        # void
        # timer_disable_counter (
        #   uint32_t timer_peripheral
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/lib/stm32/common/timer_common_all.c#L447
        timer_id = qemu.regs.r0
        self.start_time[timer_id] = 0.0
        log.info("Timer %i stopped" % timer_id)
        return True, 0

    @bp_handler(
        ["timer_set_master_mode",]
    )
    def hal_ok(self, qemu: ARMQemuTarget, bp_addr: int) -> HandlerReturn:
        # Associated HAL function declaration
        # void
        # timer_set_master_mode (
        #   uint32_t timer_peripheral,
        #   uint32_t mode
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/lib/stm32/common/timer_common_all.c#L533
        log.info("RCC Dummy return zero called")
        return True, 0

    @bp_handler(["timer_set_period"])
    def hal_timer_set_period(
        self, qemu: ARMQemuTarget, bp_addr: int
    ) -> HandlerReturn:
        # Associated HAL function declaration
        # void
        # timer_set_period (
        #   uint32_t timer_peripheral,
        #   uint32_t period
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/lib/stm32/common/timer_common_all.c#L683
        timer_id = qemu.regs.r0
        period = qemu.regs.r1
        self.period[timer_id] = period
        log.info("Timer %i period set to %i" % (timer_id, period))
        return True, 0

    @bp_handler(["timer_get_count"])
    def hal_timer_get_count(
        self, qemu: ARMQemuTarget, bp_addr: int
    ) -> HandlerReturn:
        # Associated HAL function declaration
        # int
        # timer_get_count (
        #   uint32_t timer_peripheral
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/d8aa2f17b02d1ae8e6c3cb9f1f64f1d8aaea4f4b/lib/stm32/common/timer_common_all.c#L82
        timer_id = qemu.regs.r0
        # Timer does not exits. Return zero.
        if timer_id not in self.start_time:
            return True, 0
        # Timer not started. Return zero.
        if self.start_time[timer_id] == 0.0:
            return True, 0
        time_ms = int(
            (time.time() - self.start_time[timer_id])
            * 1000
            / float(self.clock_div[timer_id])
        )
        log.info("Time: %i" % time_ms)
        count = int(time_ms / self.period[timer_id])
        log.info("Timer %i counted %i times" % (timer_id, count))
        return True, count
