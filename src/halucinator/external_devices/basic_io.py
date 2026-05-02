""" Bridges data between a unix domain socket and tty device in HALucinator
"""
# Copyright 2019 National Technology & Engineering Solutions of Sandia, LLC (NTESS).
# Under the terms of Contract DE-NA0003525 with NTESS, the U.S. Government retains
# certain rights in this software.


import logging
from halucinator.external_devices.ioserver import IOServer

log = logging.getLogger(__name__)


class BasicIO:
    """
    Reads and Writes BasicIO
    """

    def __init__(self, ioserver):
        self.ioserver = ioserver
        self.analog_values = {}
        self.digital_values = {}
        ioserver.register_topic(
            "Peripheral.DigitalIOModel.internal_update", self.digital_write_handler
        )
        ioserver.register_topic(
            "Peripheral.AnalogIOModel.internal_update", self.analog_write_handler
        )

    def digital_write_handler(self, ioserver, msg):  # pylint: disable=unused-argument
        """
        Receives Digital IO Updates
        """
        self.digital_values[msg["id"]] = msg["value"]

    def analog_write_handler(self, ioserver, msg):  # pylint: disable=unused-argument
        """
        Receives Analog IO updates
        """
        self.analog_values[msg["id"]] = msg["value"]

    def send_digital_data(self, channel_id, value):
        """
        Sends data to DigitalIOModel
        """
        msg = {"id": channel_id, "value": value}
        self.ioserver.send_msg("Peripheral.DigitalIOModel.external_update", msg)

    def send_analog_data(self, channel_id, value):
        """
        Send data to AnalogIOModel
        """
        msg = {"id": channel_id, "value": value}
        self.ioserver.send_msg("Peripheral.AnalogIOModel.external_update", msg)

    def print_digital_values(self):
        """
        Prints the received digital values
        """
        for key, value in self.digital_values.items():
            print(f"Digital: {key}: {value}")

    def print_analog_values(self):
        """
        Prints the received analog values
        """
        for key, value in self.analog_values.items():
            print(f"Analog: {key}: {value}")

    def print_values(self):
        """
        Prints analog and digital values
        """
        self.print_analog_values()
        self.print_digital_values()


if __name__ == "__main__":
    from argparse import ArgumentParser

    p = ArgumentParser()
    IOServer.add_args(p)
    args = p.parse_args()

    from halucinator import hal_log

    hal_log.setLogConfig()

    io_server = IOServer(parser_args=args)
    basic_dev = BasicIO(io_server)

    # pylint: disable=duplicate-code
    io_server.start()

    print(
        """Enter Command:
             d:<id>:<value>    Send digital value
             a:<id>:<float>    Send analog value
             p                 Print all values
             pa                Print analog values
             pd                Print digital values"""
    )
    try:
        while 1:
            input_str = input(">")
            cmds = input_str.split(":")
            cmd = None  # pylint: disable=invalid-name
            if len(cmds) >= 1:
                cmd = cmds[0].strip().lower()
            if len(cmds) == 3:
                chan_id = cmds[1].strip()
                in_value = cmds[2].strip()

            if cmd == "p":
                basic_dev.print_values()
            elif cmd == "pa":
                basic_dev.print_analog_values()
            elif cmd == "pd":
                basic_dev.print_analog_values()
            elif cmd == "a" and len(cmds) == 3:
                basic_dev.send_analog_data(chan_id, float(in_value))
            elif cmd in "d" and len(cmds) == 3:
                basic_dev.send_digital_data(chan_id, int(in_value))
            else:
                print("Invalid Command")

    except KeyboardInterrupt:
        pass
    log.info("Shutting Down")
    io_server.shutdown()

    # pylint: enable=duplicate-code
