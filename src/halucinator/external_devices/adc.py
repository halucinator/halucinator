# Copyright 2022 GrammaTech Inc.

from __future__ import annotations

import time
from multiprocessing import Process

import zmq

from halucinator import markers
from halucinator.peripheral_models.peripheral_server import (
    decode_zmq_msg,
    encode_zmq_msg,
)

__run_server = True


def rx_from_emulator(emu_rx_port: int) -> None:
    """
        Receives 0mq messages from emu_rx_port
        args:
            emu_rx_port:  The port number on which to listen for messages from
                          the emulated software
    """
    global __run_server
    context = zmq.Context()
    mq_socket = context.socket(zmq.SUB)
    mq_socket.connect("ipc:///tmp/Halucinator2IoServer%s" % emu_rx_port)
    mq_socket.setsockopt_string(zmq.SUBSCRIBE, "Peripheral.ADC.adc_write")
    # mq_socket.setsockopt_string(zmq.SUBSCRIBE, "")

    print("Setup ADC Listener")
    while __run_server:
        msg = mq_socket.recv_string()
        print("Got from emulator:", msg)
        topic, data = decode_zmq_msg(msg)
        print("Id: ", data["adc_id"], "Value", data["value"])


def update_adc(emu_tx_port: int) -> None:
    global __run_server
    global __host_socket
    topic = "Peripheral.ADC.ext_adc_change"
    context = zmq.Context()
    to_emu_socket = context.socket(zmq.PUB)
    to_emu_socket.bind("tcp://*:%s" % emu_tx_port)

    try:
        while 1:
            time.sleep(0.2)
            # Prompt for pin and value
            adc_id = input("Id: ")
            value = input("Value: ")
            data = {"adc_id": adc_id, "value": value}
            msg = encode_zmq_msg(topic, data)
            to_emu_socket.send_string(msg)
    except KeyboardInterrupt:
        __run_server = False


def start(emu_rx_port: int = 5556, emu_tx_port: int = 5555) -> None:
    markers.BUG("interface should be removed from the signature")
    global __run_server
    # print("Host socket setup")

    emu_rx_process = Process(target=rx_from_emulator, args=(emu_rx_port,))
    emu_rx_process.start()
    update_adc(emu_tx_port)
    emu_rx_process.join()


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

    args = p.parse_args()
    # TODO: Update to use IOServer Class
    start(args.rx_port, args.tx_port)


if __name__ == "__main__":
    main()
