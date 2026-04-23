# Copyright 2019 National Technology & Engineering Solutions of Sandia, LLC
# (NTESS). Under the terms of Contract DE-NA0003525 with NTESS,
# the U.S. Government retains certain rights in this software.

from __future__ import annotations

import binascii
from threading import Thread, Event
import logging
import time
import socket
from typing import Any, Callable, Dict, Mapping, Optional, TextIO, Union

import zmq
import scapy.all as scapy
import os

from halucinator.peripheral_models.peripheral_server import (
    decode_zmq_msg,
    encode_zmq_msg,
)

log = logging.getLogger(__name__)


Handler = Callable[["IOServer", Any], None]


class IOServer(Thread):
    def __init__(
        self,
        rx_port: int = 5556,
        tx_port: int = 5555,
        log_file: Optional[str] = None,
    ) -> None:
        Thread.__init__(self)
        self.rx_port: int = rx_port
        self.tx_port: int = tx_port
        self.__stop: Event = Event()
        self.context: zmq.Context[Any] = zmq.Context()
        self.rx_socket: Any = self.context.socket(zmq.SUB)
        self.rx_socket.connect("tcp://localhost:%s" % self.rx_port)
        self.tx_socket: Any = self.context.socket(zmq.PUB)
        self.tx_socket.bind("tcp://*:%s" % self.tx_port)
        self.handlers: Dict[str, Handler] = {}
        self.packet_log: Optional[TextIO] = None
        if log_file is not None:
            self.packet_log = open(log_file, "wt")
            self.packet_log.write("Direction, Time, Topic, Data\n")

    def register_topic(self, topic: str, method: Handler) -> None:
        log.debug("Registering RX_Port: %s, Topic: %s" % (self.rx_port, topic))
        self.rx_socket.setsockopt(zmq.SUBSCRIBE, topic)
        self.handlers[topic] = method

    def run(self) -> None:
        while not self.__stop.is_set():
            msg = self.rx_socket.recv_string()
            log.debug("Received: %s" % str(msg))
            topic, data = decode_zmq_msg(msg)
            if self.packet_log:
                self.packet_log.write(
                    "Sent, %i, %s, %r\n"
                    % (time.time(), topic, binascii.hexlify(data["frame"]))
                )
                self.packet_log.flush()
            method = self.handlers[topic]
            method(self, data)

    def shutdown(self) -> None:
        self.__stop.set()
        if self.packet_log:
            self.packet_log.close()

    def send_msg(self, topic: str, data: Any) -> None:
        msg = encode_zmq_msg(topic, data)
        self.tx_socket.send_string(msg)
        if self.packet_log:
            if "frame" in data:
                self.packet_log.write(
                    "Received, %i, %s, %r\n"
                    % (time.time(), topic, binascii.hexlify(data["frame"]))
                )
                self.packet_log.flush()


class HostEthernetServer(Thread):
    def __init__(self, interface: str, enable_rx: bool = False, msg_id: Optional[int] = None) -> None:
        Thread.__init__(self)
        self.interface: str = interface
        self.__stop: Event = Event()
        self.enable_rx: bool = enable_rx
        self.msg_id: Optional[int] = msg_id

        os.system('ip link set %s promisc on' %  # noqa: S605
                  interface)  # Set to permisucous
        ETH_P_ALL: int = 3
        self.host_socket: socket.socket = socket.socket(
            socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ALL))
        self.host_socket.bind((interface, 0))
        self.host_socket.settimeout(1.0)
        self.handler: Optional[Callable[..., Any]] = None

    def register_topic(self, topic: str, method: Callable[..., Any]) -> None:
        log.debug("Registering Host Ethernet Receiver Topic: %s" % topic)
        # self.rx_socket.setsockopt(zmq.SUBSCRIBE, topic)
        self.handler = method

    def run(self) -> None:
        while self.enable_rx and not self.__stop.is_set():
            try:
                frame = self.host_socket.recv(2048)
                data = {'interface_id': self.msg_id, 'frame': frame}
                msg = encode_zmq_msg("Peripheral.EthernetModel.rx_frame", data)
                self.handler(self, data)
            except socket.timeout:
                pass

        log.debug("Shutting Down Host Ethernet RX")

    def send_msg(self, topic: str, msg: Mapping[str, Union[int, str, bytes]]) -> None:
        frame = msg['frame']
        p = scapy.Raw(frame)
        scapy.sendp(p, iface=self.interface)

    def shutdown(self) -> None:
        log.debug("Stopping Host Ethernet Server")
        self.__stop.set()
