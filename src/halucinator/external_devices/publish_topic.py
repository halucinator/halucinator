# Copyright 2019 National Technology & Engineering Solutions of Sandia, LLC (NTESS). 
# Under the terms of Contract DE-NA0003525 with NTESS, the U.S. Government retains 
# certain rights in this software.


import zmq
from ..peripheral_models.peripheral_server import encode_zmq_msg, decode_zmq_msg
from .ioserver import IOServer
import logging
log = logging.getLogger(__name__)


class GenericPrintServer(object):
   
    def __init__(self, ioserver, subscribe_topic=None):
        self.ioserver = ioserver
        self.prev_print = None
        if subscribe_topic is not None:
            ioserver.register_topic(subscribe_topic, self.write_handler)

    def write_handler(self, ioserver, msg):

        data = ['%s: %s'%(key,data.decode('latin-1')) in msg.items()]
        print("Got: %s" %"".join(data))
        
    def send_data(self, topic, id, chars):
        d = {'interface_id': id, 'char': chars}
        log.debug("Sending Message (%s) %s" % (topic, str(d)))
        self.ioserver.send_msg(topic, d)


if __name__ == '__main__':
    from argparse import ArgumentParser
    p = ArgumentParser()
    p.add_argument('-r', '--rx_port', default=5556,
                   help='Port number to receive zmq messages for IO on')
    p.add_argument('-t', '--tx_port', default=5555,
                   help='Port number to send IO messages via zmq')
    p.add_argument('--tx_topic', default="Peripheral.UTTYModel.rx_char_or_buf")
    p.add_argument('--rx_topic', default=None)
    p.add_argument('-i', '--tx_id', default='COM1',
                   help="Id to use when sending data")
    p.add_argument('-n', '--newline', default=False, action='store_true',
                   help="Append Newline")
    args = p.parse_args()

    import halucinator.hal_log as hal_log
    hal_log.setLogConfig()
    
    io_server = IOServer(args.rx_port, args.tx_port)
    gen_server = GenericPrintServer(io_server, args.rx_topic)

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
            
            gen_server.send_data(args.tx_topic, args.tx_id, data)
    except KeyboardInterrupt:
        pass
    log.info("Shutting Down")
    io_server.shutdown()
    # io_server.join()
