"""Module to track buffer overflows"""

import random
from halucinator.peripheral_models import canary
from halucinator.bp_handlers.bp_handler import BPHandler, bp_handler


class Alloc(BPHandler):
    """
    Allocates additional memory and monitors it to check for buffer overflows

    Halucinator configuration usage:
    - class: halucinator.bp_handlers.generic.heap_tracking.Alloc
      function: <func_name> (Can be malloc, calloc, realloc, or free)
      class_args: {use_cookies: true/false}
      symbol: <symbol>
    """

    def __init__(self, use_cookie=False):
        self.item_size = {}  # holds the size of the extra regions
        # is always 4 for malloc
        # for calloc it is the same as the "size" argument
        self.memory_size = (
            {}
        )  # holds the size of the allocated memory from the program's perspective
        self.watchpoint = {}  # holds the watchpoint numbers
        self.is_valid = (
            {}
        )  # is used for tracking if a region of memory has been allocated/freed
        self.cookie = {}  # holds the cookie values
        self.use_cookie = use_cookie
        self.model = canary.CanaryModel

    @bp_handler(["malloc"])
    def malloc(self, qemu, addr):  # pylint: disable=unused-argument
        """
        Intercepts the malloc request and increases its sizy by 8 bytes
        then sets a breakpoint to handle the return
        """

        # intercept the size argument and increase by 8 for the cookies or watchpoints
        requested_size = qemu.get_arg(0)  # intercept the size argument
        link_reg = qemu.regs.lr

        # move the values to where the alloc_register_handler can access them
        self.item_size[link_reg] = 4
        self.memory_size[link_reg] = requested_size

        # increase the size of the memory by 4 bytes on each side
        new_size = requested_size + 8
        qemu.regs.r0 = new_size  # replace the argument to malloc

        # set a one time breakpoint on the return address to handle the return value
        qemu.set_bp(link_reg, self, "alloc_return_handler", run_once=True)

        return False, 0  # let malloc execute normally

    @bp_handler(["calloc"])
    def calloc(self, qemu, addr):  # pylint: disable=unused-argument
        """
        Intercepts the calloc request and increases the number of requested
        items by 2 so that we have somewhere to place our watchpoints/cookies
        then sets a breakpoint to handle the return
        """

        item_num = qemu.get_arg(0)  # intercept the nitems arguement
        item_size = qemu.get_arg(1)  # intercept the size argument
        link_reg = qemu.regs.lr

        # move the values to where the alloc_register_handler can access them
        self.item_size[link_reg] = item_size
        # manually calculate the size of the requested memory
        self.memory_size[link_reg] = int(item_num) * int(item_size)

        # increase the requested number of items by 2 so that we have somewhere
        # to place the cookies/watchpoint
        item_num = item_num + 2
        qemu.regs.r0 = item_num  # replace the nitems argument to calloc

        # set a one time breakpoint on the return address to handle the return value
        qemu.set_bp(link_reg, self, "alloc_return_handler", run_once=True)

        return False, 0  # let calloc execute normally

    @bp_handler(["free"])
    def free(self, qemu, addr):
        """
        Either removes the watchpoint or checks to see if the cookie values have changed
        and then frees the allocated memory
        """

        src = qemu.get_arg(
            0
        )  # this is the address of the allocated region from the program's perspective

        # if the address has no entry then is hasn't been allocated,
        # so we have a free before allocation error
        if self.is_valid.get(src) is None:
            self.model.canary(
                qemu, addr, "IllegalMemAccess", "Freeing memory before it is allocated"
            )
            return True, None

        # is is_valid == False then the address has already been freed
        # and we have a double free error
        if self.is_valid[src] is not True:
            self.model.canary(
                qemu, addr, "IllegalMemAccess", "Memory has already been freed"
            )
            return True, None

        item_size = self.item_size[src]
        mem_size = self.memory_size[src]
        self.is_valid[src] = False
        qemu.set_args(
            [src - item_size]
        )  # changese the ptr to the expanded memory region's start

        if not self.use_cookie:
            qemu.remove_breakpoint(self.watchpoint[src][0])
            qemu.remove_breakpoint(self.watchpoint[src][1])
        else:
            cookie = self.cookie[src]
            pre_cookie = qemu.read_memory(src - item_size, item_size, 1)
            post_cookie = qemu.read_memory(src + mem_size, item_size, 1)
            if pre_cookie != cookie:
                self.model.canary(
                    qemu, addr, "IllegalMemAccess", "Buffer underflow detected"
                )
            if post_cookie != cookie:
                self.model.canary(
                    qemu, addr, "IllegalMemAccess", "Buffer overflow detected"
                )
        return False, None  # let free execute normally

    @bp_handler(["alloc_return_handler"])
    def alloc_return_handler(self, qemu, addr):
        """
        Either sets the watchpoints or writes the cookie values
        """

        src = qemu.regs.r0  # the address of the expanded memory region
        mem_size = self.memory_size[addr]
        item_size = self.item_size[addr]

        # move the variables to their address from the program's perspective
        # so that free/realloc can access them
        self.memory_size[src + item_size] = mem_size
        self.item_size[src + item_size] = item_size
        self.is_valid[
            src + item_size
        ] = True  # mark the address from the program's perspective as Valid

        if not self.use_cookie:
            wp1 = qemu.set_bp(
                src, self, "handle_overflow", run_once=False, watchpoint="rw"
            )
            wp2 = qemu.set_bp(
                src + item_size + mem_size,
                self,
                "handle_overflow",
                run_once=False,
                watchpoint="rw",
            )
            self.watchpoint[src + item_size] = (
                wp1,
                wp2,
            )  # put the bp numbers where free can access it later
        else:
            bit_size = item_size * 8
            cookie = random.randint(
                0, (2**bit_size) - 1
            )  # max value for a n byte number
            qemu.write_memory(src, item_size, cookie, num_words=1, raw=False)
            qemu.write_memory(
                src + item_size + mem_size, item_size, cookie, num_words=1, raw=False
            )
            self.cookie[
                src + item_size
            ] = cookie  # put the cookie where free can access it later

        qemu.set_args(
            [src + item_size]
        )  # change the ptr address back so that the extra memory region isn't accessed
        return False, None

    @bp_handler(["realloc"])
    def realloc(self, qemu, addr):  # pylint: disable=unused-argument
        """
        Intercepts the realloc request and increases its size to account for the extra
        regions and changes the ptr value back to the origin of the expanded
        memory region
        """

        src = qemu.get_arg(0)  # get the *ptr argument
        new_size = qemu.get_arg(1)  # get the size argument
        link_reg = qemu.regs.lr
        item_size = self.item_size[src]
        mem_size = self.memory_size[src]

        # remove the upper watchpoint so it doesn't trigger on the realloc
        if not self.use_cookie:
            qemu.remove_breakpoint(self.watchpoint[src][0])
            qemu.remove_breakpoint(self.watchpoint[src][1])
        # overwrite the original end value to 0
        qemu.write_memory(src + mem_size, item_size, 0, num_words=1, raw=False)

        # move the variables to where the realloc return handler can access them
        self.memory_size[link_reg] = new_size
        self.item_size[link_reg] = item_size

        if not self.use_cookie:
            self.watchpoint[link_reg] = self.watchpoint[src]
        else:
            self.cookie[link_reg] = self.cookie[src]

        # set the pointer back to the beginning of the expanded memory region
        qemu.regs.r0 = src - item_size
        # increase the new size argument to account for the extra regions for the
        # cookies/watchpoints
        qemu.regs.r1 = new_size + 2 * item_size

        # set a one time breakpoint on the return address to handle the return value
        qemu.set_bp(link_reg, self, "realloc_return_handler", run_once=True)
        return False, None  # Let realloc execute normally

    @bp_handler(["realloc_return_handler"])
    def realloc_return_handler(self, qemu, addr):
        """
        Sets the watchpoints or cookies, and moves the ptr back so that
        the extra memory isn't accessed
        """

        src = qemu.regs.r0  # the start address of the expanded memory region
        item_size = self.item_size[addr]
        mem_size = self.memory_size[addr]

        # put the variables where free can access them
        self.memory_size[src + item_size] = mem_size
        self.item_size[src + item_size] = item_size
        self.is_valid[
            src + item_size
        ] = True  # Mark the address from the progran's perspective as valid

        if not self.use_cookie:
            wp1 = qemu.set_bp(
                src, self, "handle_overflow", run_once=False, watchpoint="rw"
            )
            wp2 = qemu.set_bp(
                src + item_size + mem_size,
                self,
                "handle_overflow",
                run_once=False,
                watchpoint="rw",
            )
            self.watchpoint[src + item_size] = (wp1, wp2)
        else:
            cookie = self.cookie[addr]
            self.cookie[src + item_size] = cookie
            # the first cookie will carry over when realloc is called, so we only need
            # to rewrite the cookie at the end of the array
            qemu.write_memory(
                src + item_size + mem_size, item_size, cookie, num_words=1, raw=False
            )

        qemu.set_args(
            [src + item_size]
        )  # change the ptr address back so that the extra memory region isn't accessed

        return False, None

    @bp_handler(["handle_overflow"])
    def handle_overflow(self, qemu, addr):
        """
        When the watchpoint is triggered it calls the
        canary peripheral
        """
        self.model.canary(qemu, addr, "IllegalMemAccess", "Buffer overflow detected")
        return False, 0
