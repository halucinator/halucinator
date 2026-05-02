"""
The peripheral model for handling canaries
"""
import logging

# from collections import deque

from halucinator.peripheral_models import peripheral_server

# from .interrupts import Interrupts


class CustomFormatter(logging.Formatter):
    """Class to color our logging to make it more obvious"""

    # pylint: disable=duplicate-code
    grey = "\x1b[38;20m"
    yellow = "\x1b[33;20m"
    red = "\x1b[31;20m"
    bold_red = "\x1b[31;1m"
    reset = "\x1b[0m"
    pr_format = (
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s (%(filename)s:%(lineno)d)"
    )

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


log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)
ch = logging.StreamHandler()
ch.setLevel(logging.DEBUG)
ch.setFormatter(CustomFormatter())
log.addHandler(ch)


# Register the pub/sub calls and methods that need to be mapped
@peripheral_server.peripheral_model
class CanaryModel:  # pylint: disable=too-few-public-methods
    """
    The peripheral model for handling canaries
    """

    interfaces = {}

    @classmethod
    @peripheral_server.tx_msg
    def canary(cls, qemu, bp_addr, canary_type, msg):
        """
        returns a dictionary with information about the function that
        triggered the canary
        """
        log.critical(
            "A %s canary was triggered by %s!\n\n/r",
            canary_type,
            qemu.get_symbol_name(bp_addr),
        )
        return {
            "Symbol": qemu.get_symbol_name(bp_addr),
            "bp_addr": bp_addr,
            "canary_type": canary_type,
            "msg": msg,
        }
