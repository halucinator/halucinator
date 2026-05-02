"""
This is the external device to handle the canary
events after they are triggered by the canary peripheral
"""

import logging
import sys
from halucinator.external_devices.ioserver import IOServer


class CustomFormatter(logging.Formatter):
    """Class to color our logging to make it more obvious"""

    # pylint: disable=duplicate-code
    grey = "\x1b[38;20m"
    yellow = "\x1b[33;20m"
    red = "\x1b[31;20m"
    bold_red = "\x1b[31;1m"
    reset = "\x1b[0m"
    pr_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s "

    FORMATS = {
        logging.DEBUG: grey + pr_format + reset,
        logging.INFO: grey + pr_format + reset,
        logging.WARNING: yellow + pr_format + reset,
        logging.ERROR: red + pr_format + reset,
        logging.CRITICAL: bold_red + pr_format + reset,
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt)
        return formatter.format(record)


# pylint: disable=duplicate-code
log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)


class CanaryDevice:  # pylint: disable=too-few-public-methods
    """
    This is the external device to handle the canary
    events after they are triggered by the canary peripheral
    """

    def __init__(self, ioserver):
        self.ioserver = ioserver
        topic = "Peripheral.CanaryModel.canary"

        self.canary_log = logging.getLogger("Canary.Sensitive.Function")

        stdout_handler = logging.StreamHandler(sys.stdout)
        stdout_handler.setLevel(logging.DEBUG)
        stdout_handler.setFormatter(CustomFormatter())
        self.canary_log.addHandler(stdout_handler)
        self.canary_log.setLevel(logging.DEBUG)
        # log.info("Registering for topic %s", topic)
        ioserver.register_topic(topic, self.write_handler)

    def write_handler(
        self, ioserver, msg
    ):  # pylint: disable=unused-argument, no-self-use
        """
        Prints out information about the canary and the
        function that triggered it
        """
        # log.info("Got status %s", str(msg))
        # log.info("bp_addr is : %s", msg["bp_addr"])
        # log.info("symbol is: %s", msg["Symbol"])
        # print("Type: %s - %s", msg["canary_type"], msg["msg"])
        self.canary_log.critical("Type: %s - %s", msg["canary_type"], msg["msg"])


# pylint: disable=duplicate-code
if __name__ == "__main__":
    from argparse import ArgumentParser

    p = ArgumentParser()
    p.add_argument(
        "-r",
        "--rx_port",
        default=5556,
        help="Port number to receive zmq messages for IO on",
    )
    p.add_argument(
        "-t", "--tx_port", default=5555, help="Port number to send IO messages via zmq"
    )
    p.add_argument(
        "-i", "--id", default=0x20000AB0, type=int, help="Id to use when sending data"
    )
    p.add_argument(
        "-n", "--newline", default=False, action="store_true", help="Append Newline"
    )
    args = p.parse_args()

    io_server = IOServer(args.rx_port, args.tx_port)
    canary = CanaryDevice(io_server)
    io_server.start()

    try:
        while 1:
            continue
    except KeyboardInterrupt:
        pass
    log.info("Shutting Down")
    io_server.shutdown()
