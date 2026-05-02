# Copyright 2019 National Technology & Engineering Solutions of Sandia, LLC (NTESS). 
# Under the terms of Contract DE-NA0003525 with NTESS, the U.S. Government retains 
# certain rights in this software.

from .ioserver import IOServer
from .trigger_interrupt import SendInterrupt
from .ethernet_virt_hub import VirtualEthHub
from .host_ethernet_server import HostEthernetServer
from threading import Thread, Event
import logging
import time
import socket
import scapy.all as scapy
import os

log = logging.getLogger(__name__)


class ARPSender(object):
    def __init__(self, ioserver, host_interface=None):
        '''
            args:
            :param ioserver:        The zeromq IO server to use for sending and 
                                    receiveing messages
            :param host_interface:  The name of the host ethernet interface to 
                                    connect to. If present all frames sent or 
                                    received are forwarded on it.
        '''
        self.ioserver = ioserver
        self.ioserver.register_topic('Peripheral.EthernetModel.tx_frame',
                                      self.received_frame)

        if host_interface is not None:
            self.host_eth = HostEthernetServer(host_interface, False)
        else:
            self.host_eth = None

    def send_request(self, interface_id):
        src_mac = '00:11:22:aa:bb:cc'
        eth_frame = scapy.Ether(dst='ff:ff:ff:ff:ff:ff', src=src_mac, type=0x806)
        arp = scapy.ARP(hwtype=0x1, ptype=0x800, hwlen=6, plen=4, op='who-has', 
                    hwsrc=src_mac, psrc='192.168.128.120', hwdst='00:00:00:00:00:00',
                    pdst='192.168.128.100')
        eth_frame.add_payload(arp)
        arp_request = eth_frame.build()
        msg = {'interface_id': interface_id, 'frame': arp_request}
        log.debug("Sending Message %s" % (str(msg)))
        self.ioserver.send_msg('Peripheral.EthernetModel.rx_frame', msg)
        if self.host_eth:
            self.host_eth.send_msg(None, msg)

    def received_frame(self, from_server, msg):
        interface = msg['interface_id']
        frame = scapy.Ether(msg['frame'])
        print("%s: %s", (interface, frame))
        if self.host_eth:
            self.host_eth.send_msg(None, msg)

    def shutdown(self):
        if self.host_eth:
            self.host_eth.shutdown()

if __name__ == '__main__':
    from argparse import ArgumentParser
    p = ArgumentParser()
    p.add_argument('-r', '--rx_port', default=5556,
                   help='Port number to receive zmq messages for IO on')
    p.add_argument('-t', '--tx_port', default=5555,
                   help='Port numbers to send IO messages via zmq, lenght must match --rx_ports')
    p.add_argument('-i', '--interface', default='eth0',
                   help='Ethernet Interace in halucinator to send to')
    p.add_argument('--host',
                   help='Host Ethernet interace to mirror frames on')
    args = p.parse_args()


    import halucinator.hal_log as hal_log
    hal_log.setLogConfig()
    
    log.setLevel(logging.DEBUG)

    io_server = IOServer(args.rx_port, args.tx_port)
    arp = ARPSender(io_server, args.host)

    io_server.start()

    try:
        while(1):
            data = input('Press Enter to Send Arp Request')
            arp.send_request(args.interface)
    except KeyboardInterrupt:
        pass
    log.info("Shutting Down")
    arp.shutdown()
    io_server.shutdown()
    # io_server.join()
