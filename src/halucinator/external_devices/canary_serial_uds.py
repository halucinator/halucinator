"Listens for canaries while forwarding between tty device and unix domain socket"
# Copyright 2022 National Technology & Engineering Solutions of Sandia, LLC (NTESS).
# Under the terms of Contract DE-NA0003525 with NTESS, the U.S. Government retains
# certain rights in this software.


import logging

from halucinator.external_devices.ioserver import IOServer
from halucinator.external_devices.canary import CanaryDevice
from halucinator.external_devices.serial_uds import UDSTunnel


log = logging.getLogger(__name__)


if __name__ == "__main__":
    from argparse import ArgumentParser

    p = ArgumentParser()
    IOServer.add_args(p)
    p.add_argument("--id", default="/utyCo/1", help="Emulation Interace to listen to")
    p.add_argument("-a", "--addr", required=True, help="Unix socket name")
    args = p.parse_args()

    from halucinator import hal_log

    hal_log.setLogConfig()

    io_server = IOServer(parser_args=args)
    uds_tunnel = UDSTunnel(io_server, args.addr, args.id)
    canary = CanaryDevice(io_server)

    io_server.start()

    try:
        while 1:
            uds_tunnel.recv_and_forward_uds_data(1)

    except KeyboardInterrupt:
        pass
    log.info("Shutting Down")
    uds_tunnel.shutdown()
    io_server.shutdown()
