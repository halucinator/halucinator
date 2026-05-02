# Copyright 2019 National Technology & Engineering Solutions of Sandia, LLC (NTESS).
# Under the terms of Contract DE-NA0003525 with NTESS, the U.S. Government retains
# certain rights in this software.

"""
BP Handlers primarily used for debugging
"""
from os import system

import logging
import IPython
from ..bp_handler import BPHandler, bp_handler


log = logging.getLogger(__name__)


class IPythonShell(BPHandler):
    """
    Drops into an IPythonShell for the breakpoint handler
    The ignore list enables watch points to ignore accesses from specific PC's

    - class: halucinator.bp_handlers.IPythonShell
      function: <func_name> (Can be anything)
      registration_args: {ignore: [symbol or addr]}
      addr: <addr>
    """

    def __init__(self):
        self.addr2name = {}
        self.ignore_list = {}

    def register_handler(self, qemu, addr, func_name, ignore=None):
        self.addr2name[addr] = func_name
        self.ignore_list[addr] = ignore if ignore is not None else []
        return IPythonShell.start_shell

    @bp_handler
    def start_shell(self, target, addr):
        """
        Starts an IPython shell if pc is not on ignore list
        """

        ignore_list = self.ignore_list[addr]
        if ignore_list:
            pc = target.regs.pc  # pylint:  disable=invalid-name
            sym = target.get_symbol_name(pc)
            if pc in ignore_list:
                return False, None

            if sym in ignore_list:
                return False, None

        log.warning("In Debug: %s", self.addr2name[addr])
        print("Execute self.print_helpers for options")
        # qemu.write_bx_lr(qemu.regs.pc)
        ret_val = None
        self.print_helpers()
        system("stty sane")  # Make so display works
        print(f"In function: {self.addr2name[addr]}")
        print(
            "You can look up a symbol using target.avatar.config.get_symbol_name(addr)"
        )
        IPython.embed()

        # return intercept, ret_val
        return False, ret_val

    def print_helpers(self):  # pylint: disable=no-self-use
        """
        Prints out the available debug helpers
        """
        print("Available Debug Helpers:")
        print("    CortexMDebugHelper(target)")


BFAR = 0xE000ED38
MMAR = 0xE000ED34


class CortexMDebugHelper:
    """
    Helper class that will decode status registers on ARM Cortex M devices
    """

    def __init__(self, qemu):
        self.qemu = qemu

    def get_mem(self, addr):
        """
        Short hand method for reading a word of memory
        """
        return self.qemu.read_memory(addr, 4, 1)

    def parse_cfsr(self, cfsr, sp_offset):  # pylint: disable=too-many-branches
        """
        Parses the configurable fault status register to human readable strings
        """
        print(f"CFSR {hex(cfsr)}")
        print("MemManage Flags")
        if cfsr & (1 << 7):
            print(f"\tMemManage Fault Address Valid: {hex(self.get_mem(MMAR))}")
        if cfsr & (1 << 5):
            print(
                "\tMemManage fault occurred during floating-point lazy state preservation"
            )
        if cfsr & (1 << 4):
            print(
                "\tStacking for an exception entry has caused one or more access violations"
            )
        if cfsr & (1 << 3):
            print(
                "\tUnstacking for an exception return has caused one or more access violations"
            )
        if cfsr & (1 << 1):
            print(
                f"\tData Access, Stacked PC {hex(self.get_stacked_pc(sp_offset))}, "
                f"Faulting Addr {hex(self.get_mem(MMAR))}"
            )
        if cfsr & (1):
            print(
                f"\tInstruction Access Violation, Stacked PC {hex(self.get_stacked_pc(sp_offset))}"
            )
        print("BusFault:")
        if cfsr & (1 << 15):
            print(f"\t Bus Fault Addr Valid {hex(self.get_mem(BFAR))}")
        if cfsr & (1 << 13):
            print("\tbus fault occurred during")
        if cfsr & (1 << 12):
            print("\tException Stacking fault")
        if cfsr & (1 << 11):
            print("\tException UnStacking fault")
        if cfsr & (1 << 10):
            print("\tImprecise data bus error, may not have location")
        if cfsr & (1 << 9):
            print(f"\tPrecise data bus error, Faulting Addr: {hex(self.get_mem(BFAR))}")
        if cfsr & (1 << 8):
            print("\tInstruction bus error")

        print("Other Faults")
        if cfsr & (1 << (9 + 16)):
            print("\tDiv by zero, Stacked PC has Addr")
        if cfsr & (1 << (8 + 16)):
            print("\tUnaligned Fault Stacking fault")
        if cfsr & (1 << (3 + 16)):
            print("\tNo Coprocessor")
        if cfsr & (1 << (2 + 16)):
            print("\tInvalid PC load UsageFault, Stacked PC has Addr")
        if cfsr & (1 << (1 + 16)):
            print("\tInvalid state UsageFault, Stacked PC has Addr")
        if cfsr & (1 << (16)):
            print("\tUndefined instruction UsageFault, Stacked PC has Addr")

    def print_exception_stack(self, offset=0):
        """
        Prints registers pushed on the stack by exception entry
        """
        #  http://infocenter.arm.com/help/index.jsp?topic=/com.arm.doc.dui0553a/Babefdjc.html
        stack_pointer = self.qemu.regs.sp
        stack_pointer += offset
        print("Registers Stacked by Exception")
        print(f"  R0: {hex(self.get_mem(stack_pointer))}")
        print(f"  R1: {hex(self.get_mem(stack_pointer + 4))}")
        print(f"  R2: {hex(self.get_mem(stack_pointer + 8))}")
        print(f"  R3: {hex(self.get_mem(stack_pointer + 12))}")
        print(f" R12: {hex(self.get_mem(stack_pointer + 16))}")
        print(f"  LR: {hex(self.get_mem(stack_pointer + 20))}")
        print(f"  PC: {hex(self.get_mem(stack_pointer + 24))}")
        print(f"xPSR: {hex(self.get_mem(stack_pointer + 28))}")

    def print_hardfault_info(self, stack_offset=0):
        """
        Prints Hardfault info, alias for print_hardfault_info
        """
        print("Configurable Fault Status Reg")
        hardfault_status = self.get_mem(0xE000ED2C)
        self.print_exception_stack(stack_offset)
        self.parse_hardfault(hardfault_status, stack_offset)

        cfsr = self.get_mem(0xE000ED28)
        self.parse_cfsr(cfsr, stack_offset)

    def hf(self, stack_offset=0):  # pylint: disable=invalid-name
        """
        Prints Hardfault info, alias for print_hardfault_info
        """
        self.print_hardfault_info(stack_offset)

    def get_stacked_pc(self, stackoffset=0):
        """
        Gets the PC pushed on the stack from in an ISR
        Offset can be used adjust if additional things have been
        pushed to stack
        """
        stack_pointer = self.qemu.regs.sp
        return self.get_mem(stack_pointer + (4 * 6) + stackoffset)

    def parse_hardfault(self, hardfault, sp_offset):
        """
        Parses the reason for a hardfault
        """
        print(f"Hard Fault 0x{hardfault} Reason: ", end=" ")
        if hardfault & (1 << 30):
            print("Forced--Other fault elavated")
        if hardfault & (1 << 1):
            print("Bus Fault")
        print(f"Stacked PC 0x{self.get_stacked_pc(sp_offset)}")
