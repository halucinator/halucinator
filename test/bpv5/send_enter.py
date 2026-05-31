import time
from halucinator.external_devices.ioserver import IOServer

server = IOServer(5556, 5555)
server.start()

time.sleep(1)

# Send '\r\n' to the BP5 interface
data = {"interface_id": "BP5", "char": [13, 10]}
server.send_msg("Peripheral.UTTYModel.rx_char_or_buf", data)

time.sleep(2)
server.shutdown()
