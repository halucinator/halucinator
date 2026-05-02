# Copyright 2020 National Technology & Engineering Solutions of Sandia, LLC (NTESS).
# Under the terms of Contract DE-NA0003525 with NTESS, the U.S. Government retains
# certain rights in this software.
"""
ARMQEMU target.  This class represents the arm machine and defines architecture specific
operations that halucinator performs. This target the ARMv4-v6 (not Cortex) 32bit architecuters
"""
from __future__ import annotations

from collections import deque

import binascii
import logging
import struct
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from avatar2 import QemuTarget

from halucinator import hal_log
from halucinator.bp_handlers import intercepts

log = logging.getLogger(__name__)
hal_log = hal_log.getHalLogger()


class AllocedMemory:
    """
    This class represents a chunck of allocated memory.  The heap returns these
    classes.  Most heap state is maintained in halucinator
    """

    def __init__(self, target: QemuTarget, base_addr: int, size: int) -> None:
        self.target: QemuTarget = target
        self.base_addr: int = base_addr
        self.size: int = size
        self.in_use: bool = True

    def zero(self) -> None:
        """
        Zero out allocated memory
        """
        zeros = "\x00" * self.size
        self.target.write_memory(self.base_addr, 1, zeros, raw=True)

    def alloc_portion(self, size: int) -> Tuple[Any, Any]:
        """
        Allocate a portion in the
        """
        if size < self.size:
            new_alloc = AllocedMemory(self.target, self.base_addr, size)
            self.base_addr += size
            self.size -= size
            return new_alloc, self
        if size == self.size:
            self.in_use = True
            return self, None
        raise ValueError(f"Trying to alloc {size} bytes from chuck of size {self.size}")

    def merge(self, block: Any) -> None:
        """
        Merges blocks with this one
        """
        self.size += block.size
        self.base_addr = (
            self.base_addr if self.base_addr <= block.base_addr else block.base_addr
        )


class ARMQemuTarget(QemuTarget):
    """
    Implements a QEMU target that has function args for use with
    halucinator.  Enables read/writing and returning from
    functions in a calling convention aware manner
    """

    # pylint: disable=too-many-public-methods

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.irq_base_addr: Optional[int] = None
        self.avatar.load_plugin("assembler")
        self.avatar.load_plugin("disassembler")
        self._init_halucinator_heap()
        self.calls_memory_blocks: Dict[Any, Any] = {}
        self.REGISTER_IRQ_OFFSET: int = 4  # pylint: disable=invalid-name

    def read_string(self, addr: int, max_len: int = 256) -> str:
        """
        Read string from target memory
        """
        ret_str = self.read_memory(addr, 1, max_len, raw=True)
        ret_str = ret_str.decode("latin-1")
        return ret_str.split("\x00")[0]

    def read_memory_word(self, addr: int) -> int:
        """
        Returns the word at the given address

        :param addr:  The address to dereference
        :returns:     The value at that address (as a 4-byte int)
        """
        value = self.read_memory(addr, 4, 1)
        assert isinstance(value, int)
        return value

    def read_memory_bytes(self, addr: int, size: int) -> bytes:
        """
        Returns bytes at the given address

        :param addr:  The address to read from
        :param size:  Number of bytes to read
        :returns:     The bytes read
        """
        return self.read_memory(addr, 1, size, raw=True)

    def write_memory_word(self, addr: int, value: int) -> bool:
        """
        Writes the given value to the given address as a 4-byte word

        :param addr:  The address to write to
        :param value: The value to write (as a 4-byte int)
        :returns:     True on success
        """
        return self.write_memory(addr, 4, value, 1)

    def write_memory_bytes(self, addr: int, value: bytes) -> bool:
        """
        Writes the given bytes to the given address

        :param addr:  The address to write to
        :param value: The bytes to write
        :returns:     True on success
        """
        return self.write_memory(addr, 1, value, len(value), raw=True)

    def dictify(self, ignore: Optional[List[str]] = None) -> Any:
        """
        Used to get dictify to ignore parts of this class to allow subclassing QemuTarget
        """
        if ignore is None:
            ignore = [
                "state",
                "status",
                "regs",
                "protocols",
                "log",
                "avatar",
                "alloced_memory",
                "free_memory",
                "calls_memory_blocks",
            ]
        super().dictify(ignore)

    def _init_halucinator_heap(self) -> None:
        """
        Initializes the scratch memory in the target that halucinator
        can use.  This requires that a 'halucinator' memory region
        exists.
        """
        for mem_name, mem_data in self.avatar.config.memories.items():
            if mem_name == "halucinator":
                heap = AllocedMemory(self, mem_data.base_addr, mem_data.size)
                heap.in_use = False
                self.alloced_memory: Set[AllocedMemory] = set()
                self.free_memory: Set[AllocedMemory] = set()
                self.free_memory.add(heap)
                return

        raise ValueError("Memory region named 'halucinator required")

    def hal_alloc(self, size: int) -> Any:
        """
        Allocates memory in the 'halucinator' memory space as a heap
        """
        if size % 4:
            size += 4 - (size % 4)  # keep aligned on 4 byte boundary
        changed_block = None
        alloced_block = None
        free_block = None
        for block in self.free_memory:
            if not block.in_use and size <= block.size:
                alloced_block, free_block = block.alloc_portion(size)
                changed_block = block
                break

        self.free_memory.remove(changed_block)
        if free_block is not None:
            self.free_memory.add(free_block)
        if alloced_block is not None:
            self.alloced_memory.add(alloced_block)
        return alloced_block

    def hal_free(self, mem: Any) -> None:
        """
        Free's memory from the halucinator heap
        """
        mem.is_used = False
        self.alloced_memory.remove(mem)
        merged_block = None
        for free_block in self.free_memory:
            # See if previous block is contiguous with this one
            if free_block.base_addr + free_block.size == mem.base_addr:
                free_block.merge(mem)
                merged_block = free_block
                break
            if mem.base_addr + mem.size == free_block.base_addr:
                free_block.merge(mem)
                merged_block = free_block
                break

        if merged_block is None:
            self.free_memory.add(mem)
        else:
            self.free_scratch_memory(merged_block)

    def get_arg(self, idx: int) -> int:
        """
        Gets the value for a function argument (zero indexed)

        :param idx  The argument index to return
        :returns    Argument value
        """
        if 0 <= idx < 4:
            return self.read_register(f"r{idx}")
        if idx >= 4:
            # pylint: disable=invalid-name
            sp = self.read_register("sp")
            stack_addr = sp + (idx - 4) * 4
            return self.read_memory(stack_addr, 4, 1)
        raise ValueError("Invalid arg index")

    def set_args(self, args: List[int]) -> int:
        """
        Sets the value for a function argument (zero indexed)

        :param args:  Iterable of args to set
        """
        for idx, value in enumerate(args[0:4]):
            if idx < 4:
                self.write_register(f"r{idx}", value)
            else:
                break

        # pylint: disable=invalid-name
        sp = self.read_register("sp")
        for idx, value in enumerate(args[:3:-1]):
            sp -= 4
            self.write_memory(sp, 4, value)

        self.write_register("sp", sp)
        return sp

    def push_lr(self) -> None:
        """
        Push lr register to stack
        """
        # pylint: disable=invalid-name
        sp = self.read_register("sp")
        sp -= 4
        self.write_memory(sp, 4, self.read_register("lr"))
        self.write_register("sp", sp)

    def get_ret_addr(self) -> int:
        """
        Gets the return address for the function call

        :returns Return address of the function call
        """
        return self.regs.lr

    def set_ret_addr(self, ret_addr: int) -> None:
        """
        Sets the return address for the function call
        :param ret_addr Value for return address
        """
        self.regs.lr = ret_addr

    def execute_return(self, ret_value: int) -> None:
        """
        Performs a function return, returning ret_value. If ret_value is none returns "void"
        """
        if ret_value is not None:
            # Puts ret value in r0
            self.regs.r0 = ret_value & 0xFFFFFFFF  # Truncate to 32 bits
        self.regs.pc = self.regs.lr

    def _get_irq_addr(self, irq_num: int) -> int:
        """
        Gets the MMIO address used for `irq_num`
        """
        if self.irq_base_addr is not None:
            return self.irq_base_addr + irq_num

        for mem_data in self.avatar.config.memories.values():
            if mem_data.qemu_name == "halucinator-irq":
                self.irq_base_addr = mem_data.base_addr + self.REGISTER_IRQ_OFFSET
                return self.irq_base_addr + irq_num
        raise (
            TypeError(
                "No Interrupt Controller found, include a memory with qemu_name: halucinator-irq"
            )
        )

    def _get_qom_list(self, path: str = "unattached") -> Any:
        """
        Returns properties for the path
        """
        # pylint: disable=unexpected-keyword-arg
        return self.protocols.monitor.execute_command("qom-list", args={"path": path})

    def _get_irq_path(self) -> Any:
        """
        Returns the qemu object model path (QOM) for the interrupt controller
        """
        for item in self._get_qom_list("unattached"):
            if item["type"] == "child<halucinator-irq>":
                log.debug("Found path %s", item["name"])
                return item["name"]
        raise (
            TypeError(
                "No Interrupt Controller found, include a memory with qemu_name: halucinator-irq"
            )
        )

    def irq_enable_qmp(self, irq_num: int = 1) -> None:
        """
        Enables interrupt using qmp.
        DO NOT execute in context of a bp handler, use irq_enable_bp instead

        :param irq_num:  The irq number to enable
        """
        path = self._get_irq_path()
        # pylint: disable=unexpected-keyword-arg
        self.protocols.monitor.execute_command(
            "qom-set", args={"path": path, "property": "enable-irq", "value": irq_num}
        )

    def irq_disable_qmp(self, irq_num: int = 1) -> None:
        """
        Disable interrupt using qmp.
        DO NOT execute in context of a bp handler, use irq_disable_bp instead

        :param irq_num:  The irq number to disable
        """
        path = self._get_irq_path()
        # pylint: disable=unexpected-keyword-arg
        self.protocols.monitor.execute_command(
            "qom-set", args={"path": path, "property": "disable-irq", "value": irq_num}
        )

    def irq_set_qmp(self, irq_num: int = 1) -> None:
        """
        Set interrupt using qmp.
        DO NOT execute in context of a bp handler, use irq_set_bp instead

        :param irq_num:  The irq number to trigger
        """
        path = self._get_irq_path()
        # pylint: disable=unexpected-keyword-arg
        self.protocols.monitor.execute_command(
            "qom-set", args={"path": path, "property": "set-irq", "value": irq_num}
        )

    def irq_clear_qmp(self, irq_num: int = 1) -> None:
        """
        Clear interrupt using qmp.
        DO NOT execute in context of a bp handler, use irq_clear_bp

        :param irq_num:  The irq number to trigger
        """

        path = self._get_irq_path()
        # pylint: disable=unexpected-keyword-arg
        self.protocols.monitor.execute_command(
            "qom-set", args={"path": path, "property": "clear-irq", "value": irq_num}
        )

    def irq_set_bp(self, irq_num: int = 1) -> None:
        """
        Set `irq_num` active using MMIO interfaces for use in bp_handlers
        """
        addr = self._get_irq_addr(irq_num)
        value = self.read_memory(addr, 1, 1)
        self.write_memory(addr, 1, value & 1)  # lowest bit controls state

    def irq_clear_bp(self, irq_num: int) -> None:
        """
        Clears `irq_num` using MMIO interface for use in bp_handlers
        """
        addr = self._get_irq_addr(irq_num)
        value = self.read_memory(addr, 1, 1)
        log.debug("Clearing IRQ BP %i", irq_num)
        self.write_memory(addr, 1, value & 0xFE)  # lowest bit controls state

    def irq_enable_bp(self, irq_num: int = 1) -> None:
        """
        Enables `irq_num` using MMIO interfaces for use in bp_handlers
        """
        addr = self._get_irq_addr(irq_num)
        value = self.read_memory(addr, 1, 1)
        self.write_memory(addr, 1, value & 0x80)  # upper most bit controls enable

    def irq_disable_bp(self, irq_num: int) -> None:
        """
        Clears `irq_num` using MMIO interface for use in bp_handlers
        """
        addr = self._get_irq_addr(irq_num)
        value = self.read_memory(addr, 1, 1)
        log.debug("Clearing IRQ BP %i", irq_num)
        self.write_memory(addr, 1, value & 0x7F)  # upper most bit controls enable

    # @deprecated(reason="Use irq_set/clear* methods instead")
    # def irq_pulse(self, irq_num=1, cpu=0):
    #     self.protocols.monitor.execute_command(
    #         "avatar-set-irq", args={"cpu_num": cpu, "irq_num": irq_num, "value": 3}
    #     )

    def get_symbol_name(self, addr: int) -> str:
        """
        Get the symbol for an address

        :param addr:    The name of a symbol whose address is wanted
        :returns:         (Symbol name on success else None
        """

        return self.avatar.config.get_symbol_name(addr)

    def set_bp(
        self, addr: int, handler_cls: Any, handler: Any, run_once: bool = False, watchpoint: bool = False
    ) -> None:  # pylint: disable=too-many-arguments
        """
        Adds a break point setting the class and method to handler it.

        :param addr:    Address of break point
        :param handler_cls:   Instance or import string for BPHandler class that
                        has handler for this bp
        :param handler: String identifing the method in handler_class to
                        handle the bp (ie. value in @bp_handler decorator)
        :param run_once:  Bool, BP should only trigger once
        :param watchpoint: one of('r','w',or 'rw') If set a watchpoint of type read, write, or rw
        """
        if isinstance(handler_cls, str):
            cls_name = handler_cls
        else:
            cls_name = (
                type(handler_cls).__module__ + "." + type(handler_cls).__qualname__
            )
        config = {
            "cls": cls_name,
            "run_once": run_once,
            "function": handler,
            "addr": addr,
            "watchpoint": watchpoint,
        }
        from halucinator import hal_config
        intercept_config = hal_config.HalInterceptConfig(__file__, **config)
        return intercepts.register_bp_handler(self, intercept_config)

    def call_varg(self, ret_bp_handler: Any, callee: Any, *args: Any) -> Any:
        """
        call a varg function in the target
        """
        raise NotImplementedError("Calling Varg functions not supported")

    def call(
        self,
        callee: Union[int, str],
        args: Optional[List[Any]] = None,
        bp_handler_cls: Union[str, Any] = None,
        ret_bp_handler: Optional[str] = None,
        debug: bool = False,
    ) -> Tuple[bool, Optional[Any]]:  # pylint: disable=too-many-arguments,too-many-locals
        """
        Calls a function in the binary and returning to ret_bp_handler.
        Using this without side effects requires conforming to calling
        convention (e.g R0-R3 have parameters and are scratch registers
        (callee save),if other registers are modified they need to be
        saved and restored)

        :param callee:   Address or name of function to be called
        :param args:     An interable containing the args to called the function
        :param bp_handler_cls:  Instance of class containing next bp_handler
                                or string for that class
        :param ret_bp_handler:  String of used in @bp_handler to identify
                                method to use for return bp_handler
        """

        if isinstance(callee, int):
            addr = callee
        else:
            addr = self.avatar.config.get_addr_for_symbol(callee)

        if addr is None:
            raise ValueError(
                "Making call to {callee}.  Address for not found for: {callee}"
            )

        key = (bp_handler_cls.__class__, ret_bp_handler, addr, len(args))
        self.push_lr()
        self.set_args(args)

        # If first time seeing this inject instructions to execute
        if key not in self.calls_memory_blocks:
            instrs = deque()
            # Build instructions in reverse order so we know offset to end
            # Where we store the address of the function to be called

            instrs.append(struct.pack("<I", addr))  # Address of callee
            instrs.append(self.assemble("mov pc, lr"))  # Return

            offset = len(
                b"".join(instrs)
            )  # PC is two instructions ahead so need to calc offset
            # two instructions before its execution
            instrs.append(self.assemble("pop {lr}"))  # Retore LR

            # Clean up stack args
            if len(args) > 4:
                stack_var_size = (len(args) - 4) * 4
                instrs.append(self.assemble(f"add sp, sp, #{stack_var_size}"))
                offset += 4

            instrs.append(self.assemble("blx lr"))  # Make Call
            instrs.append(self.assemble(f"ldr lr, [pc, #{offset}]"))  # Load Callee Addr

            instructions = b"".join(instrs)

            mem = self.hal_alloc(len(instructions))

            bytes_written = 0
            while instrs:
                bytearr = instrs.pop()
                inst_addr = mem.base_addr + bytes_written
                self.write_memory(inst_addr, 1, bytearr, len(bytearr), raw=True)

                dis = self.disassemble(inst_addr)
                dis_str = dis[0].insn_name() + " " + dis[0].op_str

                log.debug(
                    "Injected %#x:  %s\t %s ",
                    inst_addr,
                    binascii.hexlify(bytearr),
                    dis_str,
                )

                # Set break point before this function returns so new BP handler
                # can do its stuff if set
                if len(instrs) == 1:  # last "intruction written is addr"
                    if bp_handler_cls is not None and ret_bp_handler is not None:
                        self.set_bp(inst_addr, bp_handler_cls, ret_bp_handler)
                bytes_written += len(bytearr)
        else:
            mem = self.calls_memory_blocks[key]

        if debug:
            self.set_bp(
                mem.base_addr,
                "halucinator.bp_handlers.generic.debug.IPythonShell",
                "shell",
            )

        self.regs.pc = mem.base_addr
        return False, None

    def write_branch(
        self, addr: int, branch_target: int, options: Optional[Any] = None
    ) -> None:  # pylint: disable=unused-argument
        """
        Places an absolute branch at address addr to
        branch_target

        :param addr(int): Address to write the branch code to
        :param branch_target: Address to branch too
        """
        instrs = []
        instrs.append(self.assemble("ldr pc, [pc, #-4]"))  # PC is 2 instructions ahead
        instrs.append(struct.pack("<I", branch_target))  # Address of callee
        instructions = b"".join(instrs)
        self.write_memory(addr, 1, instructions, len(instructions), raw=True)
