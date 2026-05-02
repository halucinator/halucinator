"""
Implemented an IOServer for connection to HALucinator's ZMQ sockets
"""
# Copyright 2022 National Technology & Engineering Solutions of Sandia, LLC (NTESS).
# Under the terms of Contract DE-NA0003525 with NTESS, the U.S. Government retains
# certain rights in this software.


from argparse import ArgumentParser
import binascii
import logging
import time
from threading import Thread, Event
import zmq

from halucinator.peripheral_models.peripheral_server import (
    encode_zmq_msg,
    decode_zmq_msg,
)
from halucinator import hal_log

log = logging.getLogger(__name__)


class IOServer(Thread):
    """
    The IO Server connects to the ZMQ socket and sends and receives messages which
    are passed to other classes that use the IOServer
    """

    RX_PORT_ARG_STR = "rx_port"
    TX_PORT_ARG_STR = "tx_port"

    def __init__(self, rx_port=5556, tx_port=5555, log_file=None, parser_args=None):
        if parser_args is not None:
            rx_port = getattr(parser_args, IOServer.RX_PORT_ARG_STR)
            tx_port = getattr(parser_args, IOServer.TX_PORT_ARG_STR)
        Thread.__init__(self)
        self.__stop = Event()
        self.context = zmq.Context()
        io2hal_pipe = f"ipc:///tmp/Halucinator2IoServer{rx_port}"
        self.rx_socket = self.context.socket(zmq.SUB)
        self.rx_socket.connect(io2hal_pipe)
        print(f"Connected to {io2hal_pipe}")

        hal2io_pipe = f"ipc:///tmp/IoServer2Halucinator{tx_port}"
        self.tx_socket = self.context.socket(zmq.PUB)
        self.tx_socket.connect(hal2io_pipe)
        print(f"Connected to {hal2io_pipe}")

        self.poller = zmq.Poller()
        self.poller.register(self.rx_socket, zmq.POLLIN)
        self.handlers = {}
        self.packet_log = None
        if log_file is not None:
            # pylint: disable=consider-using-with
            self.packet_log = open(log_file, "wt")
            self.packet_log.write("Direction, Time, Topic, Data\n")

    def register_topic(self, topic, method):
        """
        Register the ZMQ `topic` and will call `method` when the topic is received
        """
        log.debug("Registering Topic: %s", topic)
        self.rx_socket.setsockopt(zmq.SUBSCRIBE, topic.encode("utf-8"))
        self.handlers[topic] = method

    def run(self):
        """
        The theads run routine.  Receives data for registered topics and calls the associated
        callback
        """

        while not self.__stop.is_set():
            socks = dict(self.poller.poll(1000))
            if self.rx_socket in socks and socks[self.rx_socket] == zmq.POLLIN:
                msg = self.rx_socket.recv_string()
                log.debug("Received: %s", str(msg))
                topic, data = decode_zmq_msg(msg)
                if self.packet_log:
                    self.packet_log.write(
                        "Sent, %i, %s, %s\n" % (
                            time.time(),
                            topic,
                            binascii.hexlify(data["frame"]),
                        )
                    )
                    self.packet_log.flush()
                method = self.handlers[topic]
                method(self, data)
        log.debug("IO Server Stopped")

    def shutdown(self):
        """
        Stops the IO Server and cleans up
        """
        log.debug("Stopping Host IO Server")
        self.__stop.set()
        if self.packet_log:
            self.packet_log.close()

    def send_msg(self, topic, data):
        """
        Sends a zmq message using `topic`
        """
        msg = encode_zmq_msg(topic, data)
        self.tx_socket.send_string(msg)
        if self.packet_log:
            # TODO, make logging more generic so will work for non-frames
            if "frame" in data:

                self.packet_log.write(
                    "Received, %i, %s, %s\n" % (
                        time.time(),
                        topic,
                        binascii.hexlify(data["frame"]),
                    )
                )
                self.packet_log.flush()

    @classmethod
    def add_args(cls, parser):
        """
        Adds args to an ArgumentParser to enable easily integrating into external devices
        """
        parser.add_argument(
            "-r",
            f"--{IOServer.RX_PORT_ARG_STR}",
            default=5556,
            help="Port number to receive zmq messages for IO on",
        )
        parser.add_argument(
            "-t",
            f"--{IOServer.TX_PORT_ARG_STR}",
            default=5555,
            help="Port number to send IO messages via zmq",
        )


def main():
    """
    Main
    """
    parser = ArgumentParser()
    IOServer.add_args(parser)
    args = parser.parse_args()

    hal_log.setLogConfig()

    io_server = IOServer(parser_args=args)
    io_server.start()

    try:
        while 1:
            topic = input("Topic:")
            msg_id = input("ID:")
            data = input("Data:")

            msg = {"id": msg_id, "data": data}
            io_server.send_msg(topic, msg)
    except KeyboardInterrupt:
        io_server.shutdown()
        # io_server.join()


if __name__ == "__main__":
    main()
