# Copyright 2019 National Technology & Engineering Solutions of Sandia, LLC (NTESS).
# Under the terms of Contract DE-NA0003525 with NTESS, the U.S. Government retains
# certain rights in this software.
"""
BP Handlers for Basic IO
Implemented basic handling for digital and analog input
"""
import logging
import struct
from halucinator import hal_log
from halucinator.peripheral_models.basic_io import AnalogIOModel, DigitalIOModel
from halucinator.bp_handlers.bp_handler import BPHandler, bp_handler


log = logging.getLogger(__name__)


hal_log = hal_log.getHalLogger()


class BasicIO(BPHandler):
    """
    Handlers for basic digital and analog IO
    """

    def __init__(self):
        self.digital_model = DigitalIOModel
        self.analog_model = AnalogIOModel

    @bp_handler(["read_digital"])
    def read_digital(self, target, _):
        """
        Read Digital input, assumes channel_id of input is first arg and pointer to return value
        is in second.  e.g,
        int read_digital(uint32_t i, uint8_t* value)
        """
        channel_id = target.get_arg(0)
        ret_ptr = target.get_arg(1)
        value = self.digital_model.get_value(channel_id)
        target.write_memory(ret_ptr, 1, value)
        return True, 0

    @bp_handler(["write_digital"])
    def write_digital(self, target, _):
        """
        Assumes prototype of: int write_digital(uint32_t i, uint8_t value)
        """
        channel_id = target.get_arg(0)
        value = target.get_arg(1)
        self.digital_model.set_value(channel_id, value)
        return True, 0

    @bp_handler(["read_analog"])
    def read_analog(self, target, _):
        """
        Assumes prototype of: int read_analog(uint32_t i, float *value)
        """
        channel_id = target.get_arg(0)
        ret_ptr = target.get_arg(1)
        value = self.analog_model.get_value(channel_id)
        data = struct.pack("<f", value)
        target.write_memory(ret_ptr, 4, data, raw=True)
        return True, 0

    @bp_handler(["write_analog"])
    def write_analog(self, target, bp_addr):  # pylint: disable=unused-argument
        """
        Assumes prototype of: int write_analog(uint32_t i, float value)
        """
        channel_id = target.get_arg(0)
        value = target.get_arg(1)
        value = struct.unpack("<f", struct.pack("<I", value))[0]
        value = self.analog_model.set_value(channel_id, value)
        return True, 0
