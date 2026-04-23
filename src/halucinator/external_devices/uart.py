# Copyright 2019 National Technology & Engineering Solutions of Sandia, LLC (NTESS).
# Under the terms of Contract DE-NA0003525 with NTESS, the U.S. Government retains
# certain rights in this software.

from __future__ import annotations

import zmq
from ..peripheral_models.peripheral_server import encode_zmq_msg, decode_zmq_msg
from .ioserver import IOServer
import logging
from typing import Dict, Optional, Union
log = logging.getLogger(__name__)


class UARTPrintServer(object):
   
    def __init__(self, ioserver: IOServer) -> None:
        self.ioserver: IOServer = ioserver
        self.prev_print: Optional[str] = None
        ioserver.register_topic(
            'Peripheral.UARTPublisher.write', self.write_handler)

    def write_handler(self, ioserver: IOServer, msg: Dict[str, bytes]) -> None:
        txt = msg['chars'].decode('latin-1')
        if self.prev_print == '-> ' and txt == '-> ':
            return
        else:
            self.prev_print = txt
            print("%s" % txt, end='', flush=True)

    def send_data(self, id: int, chars: str) -> None:
        d: Dict[str, Union[int, str]] = {'id': id, 'chars': chars}
        log.debug("Sending Message %s" % (str(d)))
        self.ioserver.send_msg('Peripheral.UARTPublisher.rx_data', d)


def main() -> None:
    from argparse import ArgumentParser
    p = ArgumentParser()
    p.add_argument('-r', '--rx_port', default=5556,
                   help='Port number to receive zmq messages for IO on')
    p.add_argument('-t', '--tx_port', default=5555,
                   help='Port number to send IO messages via zmq')
    p.add_argument('-i', '--id', default=0x20000ab0,
                   type=lambda x: int(x, 0),
                   help="Id to use when sending data (supports hex, e.g. 0x40013800)")
    p.add_argument('-n', '--newline', default=False, action='store_true',
                   help="Append Newline")
    p.add_argument('-v', '--verbose', default=False, action='store_true',
                   help="Show debug messages")
    args = p.parse_args()

    import halucinator.hal_log as hal_log
    hal_log.setLogConfig()

    # Default to quiet mode — only show UART data, not debug noise
    if not args.verbose:
        logging.getLogger('halucinator.external_devices').setLevel(logging.WARNING)
        logging.getLogger('halucinator.peripheral_models').setLevel(logging.WARNING)

    io_server = IOServer(args.rx_port, args.tx_port)
    uart = UARTPrintServer(io_server)

    io_server.start()

    try:
        while(1):
            data = input()
            log.debug("Got %s" % str(data))
            if args.newline:
                data +="\n"
            if data == '\\n':
                data = '\r\n'
            elif data == '':
                break
            #d = {'id':args.id, 'data': data}
            uart.send_data(args.id, data)
    except EOFError:
        # stdin closed (e.g. /dev/null) — keep server alive for receiving
        from threading import Event
        try:
            Event().wait()
        except KeyboardInterrupt:
            pass
    except KeyboardInterrupt:
        pass
    log.info("Shutting Down")
    io_server.shutdown()
    # io_server.join()


if __name__ == '__main__':
    main()
