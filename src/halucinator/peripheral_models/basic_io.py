# Copyright 2020 National Technology & Engineering Solutions of Sandia, LLC (NTESS).
# Under the terms of Contract DE-NA0003525 with NTESS, the U.S. Government retains
# certain rights in this software.
"""
Peripheral model for basic analog input and output
"""
import logging
from collections import defaultdict
from halucinator.peripheral_models import peripheral_server


log = logging.getLogger(__name__)


@peripheral_server.peripheral_model
class DigitalIOModel:
    """
    Models a Digital IO allowing bp handlers to retrieve value and send/receive a value over
    Peripheral Server
    """

    values = defaultdict(int)

    @classmethod
    def get_id(cls, channel_id):
        """
        Gets id for channel_id
        """
        try:
            channel_id = int(channel_id)
        except ValueError:
            pass
        return channel_id

    @classmethod
    def get_value(cls, channel_id):
        """
        Returns the value for given channel_id
        """
        io_id = cls.get_id(channel_id)
        return cls.values[io_id]

    @classmethod
    def set_value(cls, channel_id, value):
        """
        Saves the internal value
        """
        io_id = cls.get_id(channel_id)
        cls.internal_update(io_id, value)

    @classmethod
    @peripheral_server.tx_msg
    def internal_update(cls, channel_id, value):
        """
        Sends message indicating internal value has been updated
        """
        log.debug("Value Written %s: %s", channel_id, value)
        io_id = cls.get_id(channel_id)
        return {"id": io_id, "value": value}

    @classmethod
    @peripheral_server.reg_rx_handler
    def external_update(cls, msg):
        """
        Handle Receiption of data from Peripheral Server
        """
        log.debug("Got message: %s", str(msg))
        try:
            io_id = cls.get_id(msg["id"])
            cls.values[io_id] = msg["value"]
        except KeyError:
            log.warning("Expected Keys [id, value] got %s", msg.keys())


@peripheral_server.peripheral_model
class AnalogIOModel:
    """
    Models a Analog IO allowing bp handlers to retrieve value and send/receive a value over
    Peripheral Server
    """

    values = defaultdict(float)

    @classmethod
    def get_id(cls, channel_id):
        """
        Gets the id for the channel_id
        """
        try:
            channel_id = int(channel_id)
        except ValueError:
            pass
        return channel_id

    @classmethod
    def get_value(cls, channel_id):
        """
        Returns the value for given channel_id
        """
        io_id = cls.get_id(channel_id)
        return cls.values[io_id]

    @classmethod
    def set_value(cls, channel_id, value):
        """
        Saves the internal value
        """
        io_id = cls.get_id(channel_id)
        cls.internal_update(io_id, value)

    @classmethod
    @peripheral_server.tx_msg
    def internal_update(cls, channel_id, value):
        """
        Sends message indicating internal value has been updated
        """
        log.debug("Value Written %s: %s", channel_id, value)
        io_id = cls.get_id(channel_id)
        return {"id": io_id, "value": value}

    @classmethod
    @peripheral_server.reg_rx_handler
    def external_update(cls, msg):
        """
        Handle Receiption of data from Peripheral Server
        """
        log.debug("Got message: %s", str(msg))
        try:
            io_id = cls.get_id(msg["id"])
            cls.values[io_id] = msg["value"]
        except KeyError:
            log.warning("Expected Keys [id, value] got %s", msg.keys())
