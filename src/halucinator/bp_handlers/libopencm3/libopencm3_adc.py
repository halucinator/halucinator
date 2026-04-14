# Copyright 2022 GrammaTech Inc.
from __future__ import annotations

import logging
from typing import Type

from halucinator.bp_handlers.bp_handler import BPHandler  # type: ignore
from halucinator.bp_handlers.bp_handler import HandlerReturn, bp_handler
from halucinator.peripheral_models.adc import ADC  # type: ignore
from halucinator.qemu_targets.arm_qemu import ARMQemuTarget  # type: ignore

# Only 4 channel available.
NUMBER_OF_ADC_REGS = 4

log = logging.getLogger(__name__)


class LIBOPENCM3_ADC(BPHandler):
    def __init__(self, model: Type[ADC] = ADC) -> None:
        self.model: Type[ADC] = model

    @bp_handler(
        [
            "adc_enable_external_trigger_injected",
            "adc_reset_calibration",
            "adc_calibrate_async",
            "adc_calibrate",
            "adc_set_sample_time_on_all_channels",
            "adc_enable_scan_mode",
            "adc_enable_eoc_interrupt_injected",
            "adc_set_right_aligned",
            "adc_set_single_conversion_mode",
        ]
    )
    def hal_ok(self, qemu: ARMQemuTarget, bp_addr: int) -> HandlerReturn:
        # Associated HAL functions declaration can be found at
        # https://github.com/libopencm3/libopencm3/blob/4a378a729a9f9b7f24e527e74dd38b5ae3b9bc69/lib/stm32/common/adc_common_v1.c
        # https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/lib/stm32/f1/adc.c
        log.info("ADC Simple return zero called")
        return True, 0

    @bp_handler(["adc_power_on"])
    def hal_power_on(self, qemu: ARMQemuTarget, bp_addr: int) -> HandlerReturn:
        # Associated HAL functions declaration
        # void
        # adc_power_on (
        #   uint32_t adc
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/lib/stm32/f1/adc.c#L107
        log.info("ADC Power On Called")
        return True, 0

    @bp_handler(["adc_power_off"])
    def hal_power_off(
        self, qemu: ARMQemuTarget, bp_addr: int
    ) -> HandlerReturn:
        # Associated HAL functions declaration
        # void
        # adc_power_off (
        #   uint32_t adc
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/lib/stm32/common/adc_common_v1.c#L108
        log.info("ADC Power Off Called")
        return True, 0

    @bp_handler(["adc_is_calibrating"])
    def hal_calibrating(
        self, qemu: ARMQemuTarget, bp_addr: int
    ) -> HandlerReturn:
        # Associated HAL functions declaration
        # bool
        # adc_is_calibrating (
        #   uint32_t adc,
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/lib/stm32/f1/adc.c#L363
        log.info("ADC Calibrating Called")
        # We do not need to wait until calibration ends.
        return True, False

    @bp_handler(["adc_read_injected"])
    def hal_read_injected(
        self, qemu: ARMQemuTarget, bp_addr: int
    ) -> HandlerReturn:
        # Associated HAL functions declaration
        # uint32_t
        # adc_read_injected (
        #   uint32_t adc,
        #   uint8_t reg
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/4a378a729a9f9b7f24e527e74dd38b5ae3b9bc69/lib/stm32/common/adc_common_v1.c#L483
        log.info("ADC Read Inject Called")
        adc_id = qemu.regs.r1
        # Return zero for a wrong channel
        if not (1 <= adc_id <= NUMBER_OF_ADC_REGS):
            return True, 0
        ret_val = self.model.adc_read(adc_id)
        return True, ret_val

    @bp_handler(["adc_set_injected_sequence"])
    def hal_set_injected(
        self, qemu: ARMQemuTarget, bp_addr: int
    ) -> HandlerReturn:
        # Associated HAL functions declaration
        # void
        # adc_set_injected_sequence (
        #   uint32_t adc,
        #   uint8_t length,
        #   uint8_t channel[]
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/4a378a729a9f9b7f24e527e74dd38b5ae3b9bc69/lib/stm32/common/adc_common_v1.c#L630
        # This function does not seem to be OK. A channel value transferred in 8 bits form (as an array of uint8_t) but the channel size is 12 bits.
        # And 4 channels values stored in 32 bits variable that is written at the address of the 1st channel. It is also does not look good.

        # Crazy ADC conversion
        # define ADC_JSQR_JL_LSB		0
        # define ADC_JSQR_JL_SHIFT		0
        # define ADC_JSQR_JSQ4_LSB		26
        # define ADC_JSQR_JSQ3_LSB		20
        # define ADC_JSQR_JSQ2_LSB		14
        # define ADC_JSQR_JSQ1_LSB		8

        # define ADC_JSQR_JSQ_VAL(n, val)	((val) << (((n) - 1) * 6 + 8))
        # define ADC_JSQR_JL_VAL(val)		(((val) - 1) << ADC_JSQR_JL_SHIFT)
        # value = ADC_JSQR_JSQ_VAL(4 - i, channel[length - i - 1])
        log.info("ADC Set Inject Called")
        adc_len = qemu.regs.r1
        data_ptr = qemu.regs.r2
        if (adc_len < 1) or (adc_len > NUMBER_OF_ADC_REGS):
            return True, 0
        log.info("Read %i bytes from memory" % (adc_len))
        data = qemu.read_memory_bytes(data_ptr, adc_len)
        adc_channels_value = [x for x in data]
        value = 0
        # https://github.com/libopencm3/libopencm3/blob/4a378a729a9f9b7f24e527e74dd38b5ae3b9bc69/lib/stm32/common/adc_common_v1.c#L630
        # does not seem to be correct because all up to 4 channels packed in one 32 bits variable
        # The 12 bits value for each channel should be unpacked from the array and written at the address of the appropriate channel.
        # The loop should look like
        # for i in range(adc_len):
        #     value = <unpack_adc_channel_value>
        #     self.model.adc_write(i+1, value)
        # But we need to emulate the behaviour of the original function even if it is not correct
        for i in range(adc_len):
            value |= adc_channels_value[adc_len - i - 1] << (
                ((4 - i) - 1) * 6 + 8
            )
            value |= adc_len - 1
            # The value is 32 bits long so clear extra bits
            value &= 0xFFFFFFFF
        self.model.adc_write(1, value)
        return True, 0
