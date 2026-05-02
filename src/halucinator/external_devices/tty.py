# Copyright 2019 National Technology & Engineering Solutions of Sandia, LLC (NTESS). 
# Under the terms of Contract DE-NA0003525 with NTESS, the U.S. Government retains 
# certain rights in this software.

from os import sys, path
from ..peripheral_models.peripheral_server import encode_zmq_msg, decode_zmq_msg
import zmq
from multiprocessing import Process
from .ioserver import IOServer
import logging

import os
import socket
import time
import binascii
import scapy.all as scapy
import serial
log = logging.getLogger(__name__)

__run_server = True
__host_port = None
__rx_buffering= False

def rx_from_host(io_server, msg_id):
    global __run_server
    global __host_port
    global __rx_buffering
    topic = "Peripheral.UTTYModel.rx_char_or_buf"
    buffer=[]

    while (__run_server):
        
        if __rx_buffering:
            char = __host_port.read()
            if len(buffer) < 40:
                char_byte = int.from_bytes(char, byteorder='little')
                buffer.append(char_byte)
            else:
                char_byte = int.from_bytes(char, byteorder='little')
                buffer.append(char_byte)
                data = {'interface_id': msg_id, 'char': buffer}
                print("Sent message to emulator ", buffer)
                io_server.send_msg(topic,data)
                buffer=[]
        else:
            print("char")
            char = __host_port.read()
            print(char)
            char_byte = int.from_bytes(char, byteorder='little')
            print(char_byte)
            data = {'interface_id': msg_id, 'char': char_byte}
            print("Sent message to emulator ", binascii.hexlify(char))
            io_server.send_msg(topic,data)


def start(port, io_server, msg_id="COM1",baudrate =9600):
    global __run_server
    global __host_port
    __host_port = serial.Serial(port, baudrate)

    log.debug("Starting Servers")
    rx_from_host(io_server, msg_id)
    try:
        while (1):
            time.sleep(0.1)
    except KeyboardInterrupt:
        __run_server = False


if __name__ == '__main__':
    from argparse import ArgumentParser
    p = ArgumentParser()
    p.add_argument('-r', '--rx_port', default=5556,
                   help='Port number to receive zmq messages for IO on')
    p.add_argument('-t', '--tx_port', default=5555,
                   help='Port number to send IO messages via zmq')
    p.add_argument('-p', '--port', required=True,
                   help='Host serial port to listen to')
    p.add_argument('--id', default="COM1",
                   help='Emulation Interace to listen to')
    p.add_argument('-b','--baud', default=9600,
                   help='Baud rate')
    args = p.parse_args()
    io_server = IOServer(args.rx_port, args.tx_port)
    time.sleep(1)

    start(args.port, io_server, args.id,args.baud)
