# Copyright 2019 National Technology & Engineering Solutions of Sandia, LLC (NTESS).
# Under the terms of Contract DE-NA0003525 with NTESS, the U.S. Government retains
# certain rights in this software.
"""
Give interactive like terminal access an interface.  Prints received
data to the console and sends data typed to the target
"""


import logging
from binascii import hexlify
from halucinator import hal_log
from .ioserver import IOServer


log = logging.getLogger(__name__)


class TyConsole:
    """
    Class for sending and receiving data to the UTTY devices from terminal
    """

    def __init__(self, ioserver, outfile=None):
        self.ioserver = ioserver
        self.prev_print = ""
        ioserver.register_topic("Peripheral.UTTYModel.tx_buf", self.write_handler)
        if outfile is not None:
            self.outfd = open(outfile, "wb")  # pylint: disable=consider-using-with
        else:
            self.outfd = None

    def write_handler(self, ioserver, msg):  # pylint: disable=unused-argument
        """
        Handles Received messages from the zmq interface
        """
        if self.outfd:
            self.outfd.write(msg["chars"])
        try:
            txt = msg["chars"].decode("utf-8")
        except UnicodeDecodeError:
            txt = f"Decode Error: {hexlify(msg['chars'])}\n"
        if self.prev_print.strip() == "->" and (txt == "\n" or txt.strip() == "->"):
            return

        self.prev_print = txt
        print(f"{txt}", end="", flush=True)

    def send_data(self, msg_id, chars):
        """
        Sends data over the zmq interface
        """
        data = {"interface_id": msg_id, "char": chars}
        self.ioserver.send_msg("Peripheral.UTTYModel.rx_char_or_buf", data)


if __name__ == "__main__":
    # pylint: disable=duplicate-code
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
    p.add_argument(
        "-n", "--newline", default=False, action="store_true", help="Append Newline"
    )
    p.add_argument("--log", default=None, help="Binary file to log received data too")
    args = p.parse_args()

    hal_log.setLogConfig()

    io_server = IOServer(args.rx_port, args.tx_port)
    console = TyConsole(io_server, args.log)

    io_server.start()

    try:
        while 1:
            buff = []
            # pylint: disable=invalid-name
            in_data = input()
            log.debug("Got %s", str(in_data))
            if args.newline:
                in_data += "\n"
            if in_data == "\\n":
                in_data = "\r\n"
            elif in_data == "":
                break
            # d = {'id':args.id, 'data': data}
            for char in in_data:
                buff.append(ord(char))
            console.send_data(args.id, buff)
    except KeyboardInterrupt:
        pass
    log.info("Shutting Down")
    io_server.shutdown()
    # io_server.join()
