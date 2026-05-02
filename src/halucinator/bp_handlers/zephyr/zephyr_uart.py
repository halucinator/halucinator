# Copyright 2021 National Technology & Engineering Solutions of Sandia, LLC
# (NTESS). Under the terms of Contract DE-NA0003525 with NTESS,
# the U.S. Government retains certain rights in this software.

# Created by BYU Capstone Team 44 2020-2021
# Project sponsored by National Technology & Engineering Solutions of Sandia, LLC (NTESS).

import logging
from os import path, sys

from halucinator.bp_handlers.bp_handler import BPHandler, bp_handler
from halucinator.peripheral_models.uart import UARTPublisher

log = logging.getLogger(__name__)

from halucinator import hal_log

hal_log = hal_log.getHalLogger()


class ZephyrUART(BPHandler):

    def __init__(self, impl=UARTPublisher):
        """Initialization of ZephyrUART class and tx/rx buffers

        :param impl: UART Peripheral Model, defaults to UARTPublisher
        :type impl: UARTPublisher, optional
        """
        self.model = impl
        self.tx_buf = bytes([])
        self.rx_buf = bytes([])
        self.last_write_dev = None

    @bp_handler(['uart_mcux_init'])
    def mcux_init(self, qemu, bp_addr):
        """Firmware UART initialization breakpoint handler

        :param qemu: Firmware Emulator
        :type qemu: Avatar QEMU Target
        :param bp_addr: Breakpoint handler address
        :type bp_addr: tuple
        :return: ‘False’ indicates that the firmware should continue execution from
            the breakpoint and ignore the return value of this function, ‘True’
            indicates that the firmware should use the Integer return value of this
            function instead, Integer return value provides the replacement return
            value if Boolean is ‘True’
        :rtype: boolean, int
        """
        log.info("uart_mcux_init Called")
        print("uart_mcux_init Called")
        return True, 0

    @bp_handler(['UART_GetStatusFlags'])
    def get_statusflags(self, qemu, bp_addr):
        """Firmware handler for retrieving UART device driver status flags

        :param qemu: Firmware Emulator
        :type qemu: Avatar QEMU Target
        :param bp_addr: Breakpoint handler address
        :type bp_addr: tuple
        :return: ‘False’ indicates that the firmware should continue execution from
            the breakpoint and ignore the return value of this function, ‘True’
            indicates that the firmware should use the Integer return value of this
            function instead, Integer return value provides the replacement return
            value if Boolean is ‘True’, 0x80 = Tx Buffer Empty, 0x40 = Rx Buffer Full
        :rtype: boolean, int
        """
        log.info("Get UART status flags")
        return True, 0x80

    @bp_handler(['console_getline'])
    def handle_rx_charptr(self, qemu, bp_addr):
        """Firmware handler for reading in multi-character UART input
        Reads frame out of emulated device, saves frame to memory, and returns
        memory address of frame

        :param qemu: Firmware Emulator
        :type qemu: Avatar QEMU Target
        :param bp_addr: Breakpoint handler address
        :type bp_addr: tuple
        :return: ‘False’ indicates that the firmware should continue execution from
            the breakpoint and ignore the return value of this function, ‘True’
            indicates that the firmware should use the Integer return value of this
            function instead, Integer return value provides the replacement return
            value if Boolean is ‘True’, 0 = no data was read, otherwise = memory address
        :rtype: boolean, int
        """
        # Get address of UART memory
        config = qemu.avatar.config
        uart_mem = config.memory_by_name("ram_peripheral_uart")
        if uart_mem is None:
            out_addr = 0x30000000
        else:
            out_addr = uart_mem.base_addr

        # Read one line, nonblocking
        self.rx_buf = bytes([])
        while True:
            data = self.model.read(0, count=1, block=True)
            if (len(data) >= 1 and chr(data[0]) == '\n'):
                break
            self.rx_buf += data

        self.rx_buf += bytes([0])

        # If data was read, log data and return address
        if (len(self.rx_buf) != 0):
            hal_log.info("UART RX: %s", self.rx_buf)
            qemu.write_memory(out_addr, 1, self.rx_buf, len(self.rx_buf), raw=True)
            return True, out_addr

        # Otherwise, return 0 (no data read)
        return True, 0

    @bp_handler(['uart_mcux_poll_out'])
    def handle_tx(self, qemu, bp_addr):
        """Firmware handler for transmitting UART output

        :param qemu: Firmware Emulator
        :type qemu: Avatar QEMU Target
        :param bp_addr: Breakpoint handler address
        :type bp_addr: tuple
        :return: ‘False’ indicates that the firmware should continue execution from
            the breakpoint and ignore the return value of this function, ‘True’
            indicates that the firmware should use the Integer return value of this
            function instead, Integer return value provides the replacement return
            value if Boolean is ‘True’, 0 = success
        :rtype: boolean, int
        """
        uart_dev = qemu.get_arg(0)
        p_char = qemu.get_arg(1)
        self.model.write(0, bytes([p_char]))
        self.last_write_dev = uart_dev
        return True, 0

    @bp_handler(['uart_mcux_poll_in'])
    def handle_rx(self, qemu, bp_addr):
        """Firmware handler for reading in single-character UART input
        Reads character out of emulated device, saves character to register

        :param qemu: Firmware Emulator
        :type qemu: Avatar QEMU Target
        :param bp_addr: Breakpoint handler address
        :type bp_addr: tuple
        :return: ‘False’ indicates that the firmware should continue execution from
            the breakpoint and ignore the return value of this function, ‘True’
            indicates that the firmware should use the Integer return value of this
            function instead, Integer return value provides the replacement return
            value if Boolean is ‘True’, 0 = read success, 0xFFFFFFFF = read fail
        :rtype: boolean, int
        """
        p_char = qemu.get_arg(1)

        # Read one character, nonblocking
        data = self.model.read(0, 1, count=1, block=False)

        # If data was read, log and save to register
        if (len(data) == 1):
            hal_log.info("UART RX: %s", data)
            qemu.write_memory(p_char, 1, data, len(data), raw=True)
            return True, 0

        # If data was not read, return error state
        return True, 0xFFFFFFFF

    @bp_handler(['uart_mcux_fifo_read'])
    def handle_rx_multiple(self, qemu, bp_addr):
        """Firmware handler for reading in a set amount of characters from UART input
        Reads characters out of emulated device, saves characters to register

        :param qemu: Firmware Emulator
        :type qemu: Avatar QEMU Target
        :param bp_addr: Breakpoint handler address
        :type bp_addr: tuple
        :return: ‘False’ indicates that the firmware should continue execution from
            the breakpoint and ignore the return value of this function, ‘True’
            indicates that the firmware should use the Integer return value of this
            function instead, Integer return value provides the replacement return
            value if Boolean is ‘True’, indicates the number of characters read
        :rtype: boolean, int
        """
        p_char = qemu.get_arg(1)
        count = qemu.get_arg(2)

        # Read one character, nonblocking
        data = self.model.read(0, 1, count=count, block=False)

        # If data was read, log and save to register
        if (len(data) != 0):
            hal_log.info("UART RX: %s", data)
            qemu.write_memory(p_char, 1, data, len(data), raw=True)

        # Return number of characters read
        return True, len(data)

