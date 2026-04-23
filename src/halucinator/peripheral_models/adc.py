# Copyright 2022 GrammaTech Inc.
from __future__ import annotations

import logging
from collections import defaultdict
from typing import DefaultDict

from typing_extensions import TypedDict

from halucinator.peripheral_models import peripheral_server

log = logging.getLogger(__name__)


class ADCMessage(TypedDict):
    adc_id: int
    value: int


# Register the pub/sub calls and methods that need mapped
@peripheral_server.peripheral_model
class ADC(object):

    DEFAULT = 0
    adc_state: DefaultDict[int, int] = defaultdict(int)

    @classmethod
    @peripheral_server.tx_msg
    def adc_write(cls, adc_id: int, value: int) -> ADCMessage:
        """
            Creates the message that peripheral_server.tx_msg will send on this
            event
        """
        cls.adc_state[adc_id] = value
        msg: ADCMessage = {"adc_id": adc_id, "value": value}
        log.debug("ADC.adc_write " + repr(msg))
        return msg

    @classmethod
    @peripheral_server.reg_rx_handler
    def ext_adc_change(cls, msg: ADCMessage) -> None:
        """
            Processes reception of messages from external 0mq server
        """
        adc_id = msg["adc_id"]
        value = msg["value"]
        cls.adc_state[adc_id] = value

    @classmethod
    def adc_read(cls, adc_id: int) -> int:
        return cls.adc_state[adc_id]
