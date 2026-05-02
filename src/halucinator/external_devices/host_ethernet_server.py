# Copyright 2019 National Technology & Engineering Solutions of Sandia, LLC 
# (NTESS). Under the terms of Contract DE-NA0003525 with NTESS, 
# the U.S. Government retains certain rights in this software.

from threading import Thread, Event
import logging
import time
import socket
import scapy.all as scapy
import os

log = logging.getLogger(__name__)

class HostEthernetServer(Thread):
    def __init__(self, interface, enable_rx=False):
        Thread.__init__(self)
        self.interface = interface
        self.__stop = Event()
        self.enable_rx = enable_rx

        os.system('ip link set %s promisc on' %
                  interface)  # Set to permisucous
        ETH_P_ALL = 3
        self.host_socket = socket.socket(
            socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ALL))
        self.host_socket.bind((interface, 0))
        self.host_socket.settimeout(1.0)
        self.handler = None

    def register_topic(self, topic, method):
        log.debug("Registering Host Ethernet Receiver Topic: %s" % topic)
        # self.rx_socket.setsockopt(zmq.SUBSCRIBE, topic)
        self.handler = method

    def run(self):
        while self.enable_rx and not self.__stop.is_set():
            try:
                frame = self.host_socket.recv(2048)
                data = {'interface_id': msg_id, 'frame': frame}
                msg = encode_zmq_msg(topic, data)
                self.handler(self, data)
            except socket.timeout:
                pass

        log.debug("Shutting Down Host Ethernet RX")

    def send_msg(self, topic, msg):
        frame = msg['frame']
        p = scapy.Raw(frame)
        scapy.sendp(p, iface=self.interface)

    def shutdown(self):
        log.debug("Stopping Host Ethernet Server")
        self.__stop.set()

from .ioserver import IOServer  # re-exported for backwards compat
