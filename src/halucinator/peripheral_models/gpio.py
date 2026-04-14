# Copyright 2019 National Technology & Engineering Solutions of Sandia, LLC (NTESS).
# Under the terms of Contract DE-NA0003525 with NTESS, the U.S. Government retains
# certain rights in this software.
from __future__ import annotations

from .peripheral import requires_tx_map, requires_rx_map, requires_interrupt_map
from . import peripheral_server
from collections import defaultdict
from typing import Any, DefaultDict, Dict

import logging
log = logging.getLogger(__name__)
# Register the pub/sub calls and methods that need mapped
@peripheral_server.peripheral_model
class GPIO(object):

    DEFAULT = 0
    # TODO: Should maybe be DefaultDict[int, bool] too
    gpio_state: DefaultDict[str, int] = defaultdict(int)

    @classmethod
    @peripheral_server.tx_msg
    def write_pin(cls, gpio_id: str, value: int) -> Dict[str, Any]:
        '''
            Creates the message that peripheral_server.tx_msg will send on this 
            event
        '''
        GPIO.gpio_state[gpio_id] = value
        msg = {'id': gpio_id, 'value': value}
        log.debug("GPIO.write_pin " + repr(msg))
        return msg

    @classmethod
    @peripheral_server.tx_msg
    def toggle_pin(cls, gpio_id: str) -> Dict[str, Any]:
        '''
            Creates the message that Peripheral.tx_msga will send on this 
            event
        '''
        if gpio_id not in GPIO.gpio_state:
            GPIO.gpio_state[gpio_id] = 0
        else:
            GPIO.gpio_state[gpio_id] = GPIO.gpio_state[gpio_id] ^ 1

        msg = {'id': gpio_id, 'value': GPIO.gpio_state[gpio_id]}
        log.debug("GPIO.toggle_pin " + repr(msg))
        return msg

    @classmethod
    @peripheral_server.reg_rx_handler
    def ext_pin_change(cls, msg: Dict[str, Any]) -> None:
        '''
            Processes reception of messages from external 0mq server
            type is GPIO.zmq_set_gpio
        '''
        print("GPIO.ext_pin_change", msg)
        gpio_id = msg['id']
        value = msg['value']
        GPIO.gpio_state[gpio_id] = value

    @classmethod
    def read_pin(cls, pin_id: str) -> int:
        return GPIO.gpio_state[pin_id]
