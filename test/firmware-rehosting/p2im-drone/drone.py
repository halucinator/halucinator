# Copyright 2019 National Technology & Engineering Solutions of Sandia, LLC (NTESS).
# Under the terms of Contract DE-NA0003525 with NTESS, the U.S. Government retains
# certain rights in this software.

from typing import Dict, Optional, Union
import time

from halucinator.external_devices.ioserver import IOServer
from halucinator.peripheral_models.uart import UARTWriteMessage

# See the comment in halucinator.peripheral_models.uart about
# bytes/str confusion. That applies here as well. In other words,
# `UPARTPrintServer.send_data` should really take `chars: bytes`, with
# fallout from there.

rxbuf = []
rxlen = 0

UP = '\033[1A'
CLEAR = '\x1b[2K'

class UARTPrintServer(object):
    def __init__(self, ioserver: IOServer):
        self.ioserver = ioserver
        self.prev_print: Optional[str] = None
        ioserver.register_topic(
            "Peripheral.UARTPublisher.write", self.write_handler
        )

    # 'write' here is from the perspective of the firmware -- the
    # firmware wrote something, and now this is receiving it
    def write_handler(self, ioserver: IOServer, msg: UARTWriteMessage) -> None:
        global rxbuf
        global rxlen
        txt = msg["chars"]
        if txt == b'$':
            # New message, print the old one and start a new one
            if len(rxbuf) == 12:
                alt0 = int.from_bytes(rxbuf[5], 'little')
                alt1 = int.from_bytes(rxbuf[6], 'little')
                alt2 = int.from_bytes(rxbuf[7], 'little')
                alt3 = int.from_bytes(rxbuf[8], 'little')
                altitude = alt0 + (alt1 << 8) + (alt2 << 16) + (alt3 << 24)

                # Slight blink to give cue that the number was updated
#                print(UP, end=CLEAR)
#                print("********************", flush=True)
#                time.sleep(0.05)
#                print(UP, end=CLEAR)
                print(f"$M> altitude {altitude/100} meters.")
            elif len(rxbuf) == 14:
                struct_size = int.from_bytes(rxbuf[3], 'little')
                msg_code = int.from_bytes(rxbuf[4], 'little')

                motA_0 = int.from_bytes(rxbuf[5], 'little')
                motA_1 = int.from_bytes(rxbuf[6], 'little')
                motorA = motA_0 + (motA_1 << 8)
                

                motB_0 = int.from_bytes(rxbuf[7], 'little')
                motB_1 = int.from_bytes(rxbuf[8], 'little')
                motorB = motB_0 + (motB_1 << 8)

                motC_0 = int.from_bytes(rxbuf[9], 'little')
                motC_1 = int.from_bytes(rxbuf[10], 'little')
                motorC = motC_0 + (motC_1 << 8)

                motD_0 = int.from_bytes(rxbuf[11], 'little')
                motD_1 = int.from_bytes(rxbuf[12], 'little')
                motorD = motD_0 + (motD_1 << 8)

                #print(f"$M> motors ({struct_size}:{hex(msg_code)}) {hex(motorA)} {hex(motorB)} {hex(motorC)} {hex(motorD)}.")
                print(f"$M> motors: {hex(motorA)} {hex(motorB)} {hex(motorC)} {hex(motorD)}.")
            elif len(rxbuf) > 3:
                if len(rxbuf) > 4:
                    msg_code = int.from_bytes(rxbuf[4], 'little')
                    print(f" (recd packet: code {hex(msg_code)}, size {len(rxbuf)}")
                else:
                    print(f" (recd packet with no code, size {len(rxbuf)}")

            rxlen = 0
            rxbuf = []

        rxbuf.append(txt)
        rxlen += 1


    def send_data(self, id: int, chars: str) -> None:
        # Would like to use 'd: UARTReadMessage' here, but apparently
        # MyPy doesn't figure out that UARTReadMessage is a subtype of
        # Dict[...] as below. Ah well...
        d: Dict[str, Union[int, str]] = {"id": id, "chars": chars}
        print(f"Sending Message {chars} to uart ID {hex(id)}\n")
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
        type=int,
        help="Id to use when sending data",
    )
    p.add_argument(
        "-n",
        "--newline",
        default=False,
        action="store_true",
        help="Append Newline",
    )
    args = p.parse_args()


    io_server = IOServer(args.rx_port, args.tx_port)
    uart = UARTPrintServer(io_server)

    io_server.start()

    # I found that I need a few seconds here for the server to start
    time.sleep(2)

    # Send the handshake character - any character will do,
    # the firmware waits to read a character before it will go on.
    uart.send_data(args.id, "g")

    # Now just wait for calls to the handler. Ctrl-C will shut down.
    from threading import Event
    try:
        Event().wait()
    except KeyboardInterrupt:
        pass
    
    print("Shutting Down...")
    io_server.shutdown()


if __name__ == "__main__":
    main()
