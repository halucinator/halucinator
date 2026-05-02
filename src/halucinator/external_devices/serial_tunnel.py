"""tunnel for forwarding serial data from another VM to HALucinator"""
# Copyright 2019 National Technology & Engineering Solutions of Sandia, LLC (NTESS).
# Under the terms of Contract DE-NA0003525 with NTESS, the U.S. Government retains
# certain rights in this software.


import binascii
import logging
import sys

import serial  # pylint: disable=import-error

from halucinator.external_devices.ioserver import IOServer


log = logging.getLogger(__name__)


class SerialTunnel:
    """Serial Tunnel"""

    def __init__(self, port, ioserver, baudrate, use_pipe=False):
        self.ioserver = ioserver
        self.prev_print = None
        if use_pipe:
            self.host_port = open("port", "w+")  # pylint: disable=consider-using-with
            if not self.host_port:
                print(f"Error opening file: {port}")
                sys.exit(-1)
        else:
            self.host_port = serial.Serial(port, baudrate)
        ioserver.register_topic("Peripheral.UTTYModel.tx_buf", self.write_handler)

    def write_handler(self, ioserver, msg):  # pylint: disable=unused-argument
        """handle the reading from VM (VM is writing)"""
        tx_bytes = msg["chars"]
        print(f"VM Response: {str(tx_bytes)} = {binascii.hexlify(tx_bytes)}")
        self.host_port.write(tx_bytes)

    def send_data(self, msg_id, chars):
        """send data to VM"""
        s_data = {"interface_id": msg_id, "char": chars}
        self.ioserver.send_msg("Peripheral.UTTYModel.rx_char_or_buf", s_data)


if __name__ == "__main__":
    from argparse import ArgumentParser

    p = ArgumentParser()
    p.add_argument(
        "-r",
        "--rx_port",
        default=5556,
        help="Port number to receive zmq messages for IO on",
    )
    p.add_argument(
        "-t", "--tx_port", default=5555, help="Port number to send IO messages via zmq"
    )
    p.add_argument("--id", default="COM1", help="Emulation Interace to listen to")
    p.add_argument("-p", "--port", required=True, help="Host serial port to listen to")
    p.add_argument("-b", "--baud", default=9600, help="Baud rate")
    p.add_argument(
        "-f",
        "--use_file",
        default=False,
        action="store_true",
        help="Use a file instead of serial port",
    )
    args = p.parse_args()

    from halucinator import hal_log

    hal_log.setLogConfig()

    io_server = IOServer(args.rx_port, args.tx_port)
    serial = SerialTunnel(args.port, io_server, args.baud, args.use_file)

    io_server.start()

    try:
        while 1:
            data = serial.host_port.read(1)
            if len(data) > 0:
                print(f"Input: b'\\x{data.hex()}'")
                log.debug("Input: b'\\x%s'", data.hex())
                serial.send_data(args.id, [data])

    except KeyboardInterrupt:
        pass
    log.info("Shutting Down")
    io_server.shutdown()
