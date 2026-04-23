# Copyright 2022 GrammaTech Inc.
from __future__ import annotations

import logging

from halucinator.bp_handlers.bp_handler import BPHandler  # type: ignore
from halucinator.bp_handlers.bp_handler import HandlerReturn, bp_handler
from halucinator.qemu_targets.arm_qemu import ARMQemuTarget  # type: ignore

log = logging.getLogger(__name__)


class LIBOPENCM3_Other(BPHandler):
    @bp_handler(
        [
            "exti_set_trigger",
            "exti_enable_request",
            "exti_reset_request",
            "exti_get_flag_status",
            "exti_select_source",
            "null_handler",
            "reset_handler",
            "scb_reset_system",
            "nvic_enable_irq",
            "nvic_set_priority",
        ]
    )
    def hal_ok(self, qemu: ARMQemuTarget, bp_addr: int) -> HandlerReturn:
        # Associated HAL functions declaration can be found here -
        # exti_set_trigger - https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/include/libopencm3/stm32/common/exti_common_all.h#L82
        # exti_enable_request - https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/include/libopencm3/stm32/common/exti_common_all.h#L83
        # exti_reset_request - https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/include/libopencm3/stm32/common/exti_common_all.h#L85
        # exti_get_flag_status - https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/include/libopencm3/stm32/common/exti_common_all.h#L87
        # exti_select_source - https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/include/libopencm3/stm32/common/exti_common_all.h#L86
        # null_handler - https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/lib/cm3/vector.c#L37
        # reset_handler - https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/include/libopencm3/cm3/nvic.h#L167
        # scb_reset_system - https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/include/libopencm3/cm3/scb.h#L556
        # nvic_enable_irq - https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/include/libopencm3/cm3/nvic.h#L153
        # nvic_set_priority - https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/include/libopencm3/cm3/nvic.h#L159
        log.info("Others Dummy return zero called")
        return True, 0
