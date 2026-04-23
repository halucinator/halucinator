"""
Generic UART BP handlers for multi-arch e2e tests.

These handlers intercept uart_init/uart_write/uart_read calls in the
test firmware and route them through halucinator's UARTPublisher
peripheral model via zmq.
"""

import logging
from halucinator.bp_handlers.bp_handler import BPHandler, bp_handler
from halucinator.peripheral_models.uart import UARTPublisher
from halucinator import hal_log as hal_log_conf

log = logging.getLogger(__name__)
hal_log = hal_log_conf.getHalLogger()


class TestUART(BPHandler):
    """BP handler for the multi-arch UART test firmware."""

    @bp_handler(['uart_init'])
    def uart_init(self, qemu, bp_addr):
        uart_id = qemu.get_arg(0)
        log.info("uart_init(0x%x)", uart_id)
        return True, 0

    @bp_handler(['uart_write'])
    def uart_write(self, qemu, bp_addr):
        uart_id = qemu.get_arg(0)
        buf_ptr = qemu.get_arg(1)
        buf_len = qemu.get_arg(2)
        data = qemu.read_memory(buf_ptr, 1, buf_len, raw=True)
        hal_log.info("UART TX:%r", data)
        UARTPublisher.write(uart_id, data)
        return True, buf_len

    @bp_handler(['uart_read'])
    def uart_read(self, qemu, bp_addr):
        uart_id = qemu.get_arg(0)
        buf_ptr = qemu.get_arg(1)
        count = qemu.get_arg(2)
        log.info("UART RX: reading %d bytes from 0x%x", count, uart_id)
        data = UARTPublisher.read(uart_id, count, block=True)
        chars = bytes(data[:count])
        qemu.write_memory(buf_ptr, 1, chars, len(chars), raw=True)
        return True, len(chars)
