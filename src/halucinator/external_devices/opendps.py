# Copyright 2022 GrammaTech Inc.

from __future__ import annotations

import logging
import sys
from typing import Dict, Optional, Union

from halucinator.external_devices.ioserver import IOServer
from halucinator.peripheral_models.uart import UARTWriteMessage

log = logging.getLogger(__name__)

# This code implements the other end of a serial connection with OpenDPS
# It understand the packet protocol

# See the comment in halucinator.peripheral_models.uart about
# bytes/str confusion.


class UARTDPSController(object):
    def __init__(self, ioserver: IOServer) -> None:
        self.ioserver: IOServer = ioserver
        self.last_packet: Optional[str] = None
        self.send_packet: Optional[str] = None
        ioserver.register_topic(
            "Peripheral.UARTPublisher.write", self.write_handler
        )

    # 'write' here is from the perspective of the firmware -- the
    # firmware wrote something, and now this is receiving it
    def write_handler(self, ioserver: IOServer, msg: UARTWriteMessage) -> None:
        # Have to work out what decoding is necessary for these packets
        txt = msg["chars"].decode("latin-1")
        self.last_packet = txt

    def send_data(self, id: int, chars: str) -> None:
        # Would like to use 'd: UARTReadMessage' here, but apparently
        # MyPy doesn't figure out that UARTReadMessage is a subtype of
        # Dict[...] as below. Ah well...
        d: Dict[str, Union[int, str]] = {"id": id, "chars": chars}
        log.debug("Sending packet %s" % (str(d)))
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
        default=0x40013800,
        type=lambda x: int(x, 0),
        help="Id to use when sending data, default is 0x40013800 for STM32F1's USART1.SR (supports hex)",
    )
    # We may want to add an argument for 'send-illegal-packet'
    # p.add_argument(
    #    "-n",
    #    "--newline",
    #    default=False,
    #    action="store_true",
    #    help="Append Newline",
    # )
    args = p.parse_args()

    import halucinator.hal_log as hal_log

    hal_log.setLogConfig()

    io_server = IOServer(args.rx_port, args.tx_port)
    uart = UARTDPSController(io_server)

    io_server.start()

    try:
        while 1:
            if uart.last_packet is not None:
                # print(f"Would parse a packet of length {len(uart.last_packet)}")
                print(uart.last_packet, end="")
                sys.stdout.flush()
                uart.last_packet = None
            # See if we computed a response, and if so we send it.
            if uart.send_packet is not None:
                uart.send_data(args.id, uart.send_packet)
                uart.send_packet = None
    except KeyboardInterrupt:
        pass
    log.info("Shutting Down")
    io_server.shutdown()


if __name__ == "__main__":
    main()
