""" Bridges data between a unix domain socket and tty device in HALucinator
"""
# Copyright 2019 National Technology & Engineering Solutions of Sandia, LLC (NTESS).
# Under the terms of Contract DE-NA0003525 with NTESS, the U.S. Government retains
# certain rights in this software.

from __future__ import annotations

from argparse import ArgumentParser
from binascii import hexlify
import logging
import socket
from typing import Any, List, Mapping, Optional

from halucinator.external_devices.ioserver import IOServer


log = logging.getLogger(__name__)


class UDSTunnel:
    """
    Creates a Tunnel between a Unix Domain Socket and a halucinator UTTYModel peripheral
    """

    def __init__(self, ioserver: IOServer, socket_addr: str, tty_model_id: str) -> None:
        self.ioserver: IOServer = ioserver
        self.tty_model_id: str = tty_model_id
        self.prev_print: Optional[str] = None

        self.host_port: socket.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.host_port.connect(socket_addr)
        log.debug("Connected to %s", socket_addr)
        ioserver.register_topic("Peripheral.UTTYModel.tx_buf", self.write_handler)

    def write_handler(self, ioserver: IOServer, msg: Mapping[str, Any]) -> None:  # pylint: disable=unused-argument
        """
        Sends from the UTTYModel to the UDS Socket
        """
        tx_bytes = msg["chars"]
        log.debug("To VM %s ", str(tx_bytes))
        self.host_port.send(tx_bytes)

    def send_data(self, msg_id: str, chars: Any) -> None:
        """
        Sends data to the UTTYModel
        """
        msg = {"interface_id": msg_id, "char": chars}
        self.ioserver.send_msg("Peripheral.UTTYModel.rx_char_or_buf", msg)

    def recv_and_forward_uds_data(self, bytes_per_recv: int = 1) -> None:
        """
        Receives UDS data and sends UTTYModel
        """
        data = self.host_port.recv(bytes_per_recv)
        if len(data) > 0:
            print(f"From VM: {hexlify(data)}")
            log.debug("From VM %s", hexlify(data))
            self.send_data(self.tty_model_id, [data])

    def shutdown(self) -> None:
        """
        Shutdown and close the tunnel
        """
        self.host_port.close()

    @classmethod
    def add_args(cls, parser: ArgumentParser) -> None:
        """
        Adds args needed to ArgumentParser `parser`
        """
        parser.add_argument(
            "--tty_id", default="COM1", help="UTTYModel device to connect to"
        )
        parser.add_argument("-a", "--addr", required=True, help="Unix socket name")


if __name__ == "__main__":
    from argparse import ArgumentParser

    p = ArgumentParser()
    IOServer.add_args(p)
    UDSTunnel.add_args(p)
    args = p.parse_args()

    from halucinator import hal_log

    hal_log.setLogConfig()

    io_server = IOServer(parser_args=args)
    uds_tunnel = UDSTunnel(io_server, args.addr, args.tty_id)

    # pylint: disable=duplicate-code
    io_server.start()

    try:
        while 1:
            uds_tunnel.recv_and_forward_uds_data(1)

    except KeyboardInterrupt:
        pass
    log.info("Shutting Down")
    uds_tunnel.shutdown()
    io_server.shutdown()

    # pylint: enable=duplicate-code
