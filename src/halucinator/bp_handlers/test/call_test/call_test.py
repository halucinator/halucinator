# Copyright 2021 National Technology & Engineering Solutions of Sandia, LLC
# (NTESS). Under the terms of Contract DE-NA0003525 with NTESS,
# the U.S. Government retains certain rights in this software.
"""
BP_handler for testing halucinator's memory
"""

import sys
import logging

# from ...bp_handler import BPHandler, bp_handler
from halucinator.bp_handlers.bp_handler import BPHandler, bp_handler

# from .... import hal_log
from halucinator import hal_log


log = logging.getLogger(__name__)
hal_log = hal_log.getHalLogger()


class CallTest(BPHandler):
    """
    This breaks at one address, calls memcopy, then calls
    chains to another caller and calls memcopy again.

    Here's how it works.  First, init is called, it then calls memcopy
    coping data to halucinator allocated memory (hal_alloc) with setup
    to execute next_call bp_handler.  Which then copies the data from
    hal_alloc location to a new hal_alloced memory. Directing execution
    to check, which then compares the three memory locations to see if
    they are the same.
    """

    def __init__(self):
        self.original_addr = None
        self.halloced_memory1 = None
        self.halloced_memory2 = None
        self.test_str = None

    def register_handler(self, qemu, addr, func_name, test_str=None):
        self.test_str = (
            bytes(test_str, "utf-8")
            if test_str is not None
            else b"THIS IS THE TEST STRING"
        )
        return super().register_handler(qemu, addr, func_name)

    @bp_handler(["run_test"])
    def run_test(self, qemu, addr):  # pylint: disable=unused-argument
        """
        This will call memory copy the first time
        """
        print("Running Test")
        print("Creating String: %s", self.test_str)
        size = len(self.test_str)
        src = qemu.hal_alloc(size)
        qemu.write_memory(src.base_addr, 1, self.test_str, raw=True)
        print("String written to memory")
        dst = qemu.hal_alloc(size)
        self.original_addr = src
        self.halloced_memory1 = dst

        print("Executing First memcpy call")
        return qemu.call(
            "memcpy", [dst.base_addr, src.base_addr, size], self, "next_call"
        )

    @bp_handler(["next_call"])
    def next_call(self, qemu, addr):  # pylint: disable=unused-argument
        """
        This will call memory copy the first time
        """
        size = len(self.test_str)
        dst = qemu.hal_alloc(size)
        self.halloced_memory2 = dst
        src = self.halloced_memory1.base_addr

        print("Executing Second memcpy call")
        return qemu.call("memcpy", [dst.base_addr, src, size], self, "end_test")

    @bp_handler(["end_test"])
    def end_test(self, qemu, addr):  # pylint: disable=unused-argument
        """
        Check to see if copies happened
        """
        size = len(self.test_str)
        print("Got to End Test")
        original = qemu.read_memory(self.original_addr.base_addr, 1, size, raw=True)
        copy1 = qemu.read_memory(self.halloced_memory1.base_addr, 1, size, raw=True)
        copy2 = qemu.read_memory(self.halloced_memory2.base_addr, 1, size, raw=True)
        print("Original: %s", original)
        print("Copy 1: %s", copy1)
        print("Copy 2: %s", copy2)

        failed = False
        if original != copy1:
            failed = True
            print("Copy 1 Failed")

        if original != copy2:
            failed = True
            print("Copy 2 Failed")

        avatar = qemu.avatar
        avatar.stop()
        avatar.shutdown()
        # periph_server.stop()
        if failed:
            print("RESULT: FAILED")
            sys.exit(-1)
        else:
            print("RESULT: PASSED")
            sys.exit(0)
