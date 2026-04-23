# Copyright 2018 National Technology & Engineering Solutions of Sandia, LLC
# (NTESS). Under the terms of Contract DE-NA0003525 with NTESS, the U.S.
# Government retains certain rights in this software.

from __future__ import annotations

import logging
from threading import Thread
from typing import Dict, Mapping, Union

import IPython

from halucinator.external_devices.ioserver import IOServer
from halucinator.external_devices.uart import UARTPrintServer

log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)


class VN8200XP(Thread):
    def __init__(self, ioserver: IOServer) -> None:
        self.ioserver: IOServer = ioserver
        ioserver.register_topic(
            "Peripheral.UARTPublisher.write", self.write_handler
        )

    def write_handler(
        self, ioserver: IOServer, msg: Mapping[str, str]
    ) -> None:
        print((msg,))
        IPython.embed()

    def send_data(self, id: int, chars: str) -> None:
        d: Dict[str, Union[int, str]] = {"id": id, "chars": chars}
        log.debug("Sending Message %s" % (str(d)))
        self.ioserver.send_msg("Peripheral.UARTPublisher.rx_data", d)


def main() -> None:
    from argparse import ArgumentParser

    p = ArgumentParser()
    p.add_argument(
        "-r",
        "--rx_port",
        default=5556,
        help="Port number to receive zmq messages for IO on",
    )
    p.add_argument(
        "-t",
        "--tx_port",
        default=5555,
        help="Port number to send IO messages via zmq",
    )
    p.add_argument(
        "-i",
        "--id",
        default=0x20000AB0,
        type=lambda x: int(x, 0),
        help="Id to use when sending data (supports hex)",
    )
    args = p.parse_args()

    import halucinator.hal_log as hal_log

    hal_log.setLogConfig()

    io_server = IOServer(args.rx_port, args.tx_port)
    uart = UARTPrintServer(io_server)

    io_server.start()

    try:
        while 1:
            data = input("Data:")
            log.debug("Got %s" % str(data))
            if data == "\\n":
                data = "\n\r"
            elif data == "":
                break
            # d = {'id':args.id, 'data': data}
            uart.send_data(args.id, data)
    except KeyboardInterrupt:
        pass
    log.info("Shutting Down")
    io_server.shutdown()
    # io_server.join()


if __name__ == "__main__":
    main()
