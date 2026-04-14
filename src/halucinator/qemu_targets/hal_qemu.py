from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from avatar2 import QemuTarget
from halucinator import hal_config

log = logging.getLogger(__name__)


class AllocedMemory:
    def __init__(self, target: QemuTarget, base_addr: int, size: int) -> None:
        self.target: QemuTarget = target
        self.base_addr: int = base_addr
        self.size: int = size
        self.in_use: bool = True
        # TODO add ability to set watchpoint for bounds checking

    def zero(self) -> None:
        zeros = "\x00" * self.size
        self.target.write_memory(self.base_addr, 1, zeros, raw=True)

    def alloc_portion(self, size: int) -> Tuple[Any, Any]:
        if size < self.size:
            new_alloc = AllocedMemory(self.target, self.base_addr, size)
            self.base_addr += size
            self.size -= size
            return new_alloc, self
        elif size == self.size:
            self.in_use = True
            return self, None
        else:
            raise ValueError(
                "Trying to alloc %i bytes from chuck of size %i"
                % (size, self.size)
            )

    def merge(self, block: Any) -> None:
        """
            Merges blocks with this one
        """
        self.size += block.size
        self.base_addr = (
            self.base_addr
            if self.base_addr <= block.base_addr
            else block.base_addr
        )


class HALQemuTarget(QemuTarget):
    """
        Implements a QEMU target that has function args for use with
        halucinator.  Enables read/writing and returning from
        functions in a calling convention aware manner
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super(HALQemuTarget, self).__init__(*args, **kwargs)
        self.irq_base_addr: Optional[int] = None
        self.avatar.load_plugin("assembler")
        self.avatar.load_plugin("disassembler")
        self._init_halucinator_heap()
        self.calls_memory_blocks: Dict[
            Any, Any
        ] = {}  # Look up table of allocated memory
        self.REGISTER_IRQ_OFFSET: int = 4  # pylint: disable=invalid-name
        # used to perform calls

    def dictify(self, ignore: Optional[List[str]] = None) -> Dict[str, Any]:
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
        return super().dictify(ignore)

    def _init_halucinator_heap(self) -> None:
        """
            Initializes the scratch memory in the target that halucinator
            can use.  This requires that a 'halucinator' memory region
            exists.
        """
        for mem_name, mem_data in self.avatar.config.memories.items():  # type: ignore
            if mem_name == "halucinator":
                heap = AllocedMemory(self, mem_data.base_addr, mem_data.size)
                heap.in_use = False
                self.alloced_memory: Set[AllocedMemory] = set()
                self.free_memory: Set[AllocedMemory] = set()
                self.free_memory.add(heap)
                return

        raise ValueError("Memory region named 'halucinator' required")

    def hal_alloc(self, size: int) -> Any:
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

        if changed_block is not None:
            self.free_memory.remove(changed_block)

        if free_block is not None:
            self.free_memory.add(free_block)

        if alloced_block is not None:
            self.alloced_memory.add(alloced_block)

        return alloced_block

    def hal_free(self, mem: Any) -> None:
        mem.is_used = False
        self.alloced_memory.remove(mem)
        merged_block = None
        for block in self.free_memory:
            # See if previous block is contiguous with this one
            if block.base_addr + block.size == mem.base_addr:
                block.merge(mem)
                merged_block = block
                break
            elif mem.base_addr + mem.size == block.base_addr:
                block.merge(mem)
                merged_block = block
                break

        if merged_block is None:
            self.free_memory.add(mem)
        else:
            # This doesn't exist. I think it is supposed to free that block of mem
            # changing to recursive free in that case
            # self.free_scratch_memory(merged_block)
            self.alloced_memory.remove(merged_block)

    def read_string(self, addr: int, max_len: int = 256) -> str:
        s = bytes(self.read_memory(addr, 1, max_len, raw=True))
        ss = s.decode("latin-1")
        return ss.split("\x00")[0]

    def get_registers(self) -> Set[str]:
        return self.regs._get_names()  # type: ignore

    def get_arg(self, idx: int) -> int:
        """
            Gets the value for a function argument (zero indexed)
            :param idx  The argument index to return
            :returns    Argument value
        """
        raise NotImplementedError("Subclass must override this function")

    def set_args(self, args: List[int]) -> None:
        """
            Sets the value for a function argument (zero indexed)
            :param idx      The argument index to return
            :param value    Value to set index to
        """
        raise NotImplementedError("Subclass must override this function")

    def get_ret_addr(self) -> int:
        """
            Gets the return address for the function call
            :returns Return address of the function call
        """
        raise NotImplementedError("Subclass must override this function")

    def set_ret_addr(self, ret_addr: int) -> None:
        """
            Sets the return address for the function call
            :param ret_addr Value for return address
        """
        raise NotImplementedError("Subclass must override this function")

    def execute_return(self, ret_value: int) -> None:
        raise NotImplementedError("Subclass must override this function")

    def _get_irq_addr(self, irq_num: int) -> int:
        """
        Gets the MMIO address used for `irq_num`
        """
        if self.irq_base_addr is not None:
            return self.irq_base_addr + irq_num

        for mem_data in self.avatar.config.memories.values():
            if mem_data.qemu_name == "halucinator-irq":
                self.irq_base_addr = (
                    mem_data.base_addr + self.REGISTER_IRQ_OFFSET
                )
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
        return self.protocols.monitor.execute_command(
            "qom-list", args={"path": path}
        )

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
            "qom-set",
            args={"path": path, "property": "enable-irq", "value": irq_num},
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
            "qom-set",
            args={"path": path, "property": "disable-irq", "value": irq_num},
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
            "qom-set",
            args={"path": path, "property": "set-irq", "value": irq_num},
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
            "qom-set",
            args={"path": path, "property": "clear-irq", "value": irq_num},
        )

    def irq_set_bp(self, irq_num: int = 1) -> None:
        """
        Set `irq_num` active using MMIO interfaces for use in bp_handlers
        """
        addr = self._get_irq_addr(irq_num)
        value = self.read_memory(addr, 1, 1)
        self.write_memory(addr, 1, value & 1)  # lowest bit controls state

    def irq_clear_bp(self, irq_num: int = 1) -> None:
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
        self.write_memory(
            addr, 1, value & 0x80
        )  # upper most bit controls enable

    def irq_disable_bp(self, irq_num: int) -> None:
        """
        Clears `irq_num` using MMIO interface for use in bp_handlers
        """
        addr = self._get_irq_addr(irq_num)
        value = self.read_memory(addr, 1, 1)
        log.debug("Clearing IRQ BP %i", irq_num)
        self.write_memory(
            addr, 1, value & 0x7F
        )  # upper most bit controls enable

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
        # Remove the type: ignore when fixed
        return self.avatar.config.get_symbol_name(addr)  # type: ignore

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
        Returns the word at the given address

        :param addr:  The address to dereference
        :returns:     The value at that address
        """
        value = self.read_memory(addr, 1, size, raw=True)
        assert isinstance(value, bytes)
        return value

    def write_memory_word(self, addr: int, value: int) -> bool:
        """
        Writes the given value to the given address. Returns whatever
        True on success, or False

        :param addr:  The address to dereference
        :param value: The value to write (as a 4-byte int)
        :returns:     True on success
        """
        return self.write_memory(addr, 4, value)

    def write_memory_bytes(self, addr: int, value: bytes) -> bool:
        """
        Writes the given value to the given address. Returns whatever
        True on success, or False

        :param addr:  The address to dereference
        :param value: The value to write
        :returns:     True on success
        """
        return self.write_memory(addr, 1, value, len(value), raw=True)

    def set_bp(
        self,
        addr: int,
        handler_cls: Any,
        handler: Any,
        run_once: bool = False,
        watchpoint: bool = False,
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
                type(handler_cls).__module__
                + "."
                + type(handler_cls).__qualname__
            )
        config = {
            "cls": cls_name,
            "run_once": run_once,
            "function": handler,
            "addr": addr,
            "watchpoint": watchpoint,
        }
        intercept_config = hal_config.HalInterceptConfig(__file__, **config)
        from halucinator.bp_handlers import intercepts

        return intercepts.register_bp_handler(self, intercept_config)

    def call_varg(self, ret_bp_handler: Any, callee: Any, *args: Any) -> Any:
        raise NotImplementedError("Subclass must override this function")

    def call(
        self,
        callee: Any,
        args: Any = None,
        bp_handler_cls: Any = None,
        ret_bp_handler: Any = None,
        debug: bool = False,
    ) -> Any:
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
        raise NotImplementedError("Subclass must override this function")

    def write_branch(
        self, addr: int, branch_target: int, options: Optional[Any] = None
    ) -> None:
        raise NotImplementedError("Subclass must override this function")
