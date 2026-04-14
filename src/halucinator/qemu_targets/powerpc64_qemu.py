from __future__ import annotations

import binascii
import os
import struct
from collections import deque
from typing import Any, List, Optional, Tuple, Union

import yaml

from halucinator import hal_log
from halucinator.qemu_targets.hal_qemu import HALQemuTarget


class PowerPC64QemuTarget(HALQemuTarget):
    # TODO: Make this actually 64 bit, this is more of a placeholder
    """
        Implements a QEMU target that has function args for use with
        halucinator.  Enables read/writing and returning from
        functions in a calling convention aware manner
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super(PowerPC64QemuTarget, self).__init__(*args, **kwargs)

    def hal_alloc(self, size: int) -> Any:
        if size % 8:
            size += 8 - (size % 8)  # keep aligned on 8 byte boundary
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

    def get_arg(self, idx: int) -> int:
        """
            Gets the value for a function argument (zero indexed)
            This is different from other architectures,
            in that we know the calling convention for `word` length,
            not necessarily parameters (parameters can span multiple words).
            Here we just assume a word is the length of an argument.
            :param idx  The argument index to return
            :returns    Argument value
        """
        # TODO: If arguments are floating point this is incorrect, as well as if arg is 64 bit arg this is wrong
        if 0 <= idx < 8:  # first 8 are in GPR3-9
            return self.read_register("r%i" % (idx + 3))
        elif idx >= 8:
            sp = self.read_register("sp")
            stack_addr = sp + (idx - 8) * 8
            return self.read_memory_word(stack_addr)
        else:
            raise ValueError("Invalid arg index")

    def set_arg(self, idx: int, value: int) -> None:
        """
            Sets the value for a function argument (zero indexed)


            :param idx      The argument index to return
            :param value    Value to set index to
        """
        if idx >= 0 and idx < 8:
            self.write_register("r%i" % (idx + 3), value)
        elif idx >= 8:
            sp = self.read_register("sp")  # sp == r1
            stack_addr = sp + (idx - 8) * 8
            self.write_memory_word(stack_addr, value)
        else:
            raise ValueError(idx)

    def set_args(self, args: List[int]) -> int:
        """
            Sets the value for a function argument (zero indexed)

            :param args:  Iterable of args to set
        """
        for idx, value in enumerate(args[0:8]):
            if 0 <= idx < 8:
                self.write_register((f"r{idx+3}"), value)
            else:
                break

        sp = self.read_register("sp")
        for idx, value in enumerate(args[:7:-1]):
            sp -= 8
            self.write_memory(sp, 8, value)

        self.write_register("sp", sp)
        return sp

    def push_lr(self) -> None:
        sp = self.read_register("sp")
        sp -= 8
        self.write_memory(sp, 8, self.read_register("lr"))
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
        if ret_value != None:
            # Puts ret value in r3
            self.regs.r3 = ret_value
        self.regs.pc = self.regs.lr  # regs.pc == regs.nip

    def get_irq_base_addr(self) -> int:
        raise NotImplementedError

    def irq_set_qmp(self, irq_num: int = 1) -> None:
        """
            Set interrupt using qmp.
            DO NOT execute in context of a bp handler, use irq_set_bp instead

            :param irq_num:  The irq number to trigger
        """
        raise NotImplementedError

    def irq_clear_qmp(self, irq_num: int = 1) -> None:
        """
            Clear interrupt using qmp.
            DO NOT execute in context of a bp handler, use irq_clear_bp

            :param irq_num:  The irq number to trigger
        """
        raise NotImplementedError

    def irq_set_bp(self, irq_num: int = 1) -> None:
        raise NotImplementedError

    def irq_clear_bp(self, irq_num: int = 1) -> None:
        raise NotImplementedError

    def irq_pulse(self, irq_num: int = 1, cpu: int = 0) -> None:
        raise NotImplementedError

    def call(
        self,
        callee: Union[int, str],
        args: Optional[List[Any]] = None,
        bp_handler_cls: Union[str, Any] = None,
        ret_bp_handler: Optional[str] = None,
        debug: bool = False,
    ) -> Tuple[bool, Union[int, Any]]:
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

        if type(callee) == int:
            addr = callee
        else:
            addr = self.avatar.config.get_addr_for_symbol(callee)

        if addr == None:
            raise ValueError(
                "Making call to %s.  Address for not found for: %s"
                % (callee, callee)
            )

        key = (bp_handler_cls.__class__, ret_bp_handler, addr, len(args))
        self.push_lr()
        new_sp = self.set_args(args)  # type: ignore # noqa: F841

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
            if len(args) > 8:
                stack_var_size = (len(args) - 8) * 4
                instrs.append(
                    self.assemble("add sp, sp, #%i" % stack_var_size)
                )
                offset += 4

            instrs.append(self.assemble("blx lr"))  # Make Call
            instrs.append(
                self.assemble("ldr lr, [pc, #%i]" % offset)
            )  # Load Callee Addr

            instructions = b"".join(instrs)

            mem = self.hal_alloc(len(instructions))

            bytes_written = 0
            while instrs:
                bytearr = instrs.pop()
                inst_addr = mem.base_addr + bytes_written
                self.write_memory(
                    inst_addr, 1, bytearr, len(bytearr), raw=True
                )

                dis = self.disassemble(inst_addr)
                dis_str = dis[0].insn_name() + " " + dis[0].op_str

                hal_log.debug(
                    "Injected %#x:  %s\t %s "
                    % (inst_addr, binascii.hexlify(bytearr), dis_str)
                )

                # Set break point before this function returns so new BP handler
                # can do its stuff if set
                if len(instrs) == 1:  # last "intruction written is addr"
                    if (
                        bp_handler_cls is not None
                        and ret_bp_handler is not None
                    ):
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
    ) -> None:
        """
            Places an absolute branch at address addr to
            branch_target

            :param addr(int): Address to write the branch code to
            :param branch_target: Address to branch too
        """
        raise NotImplementedError
        # Need to determine if PPC can do PC relative load
        instrs = []
        instrs.append(self.assemble("l pc, 0 (pc),"))  #
        instrs.append(struct.pack(">I", branch_target))  # Address of callee
        instructions = b"".join(instrs)
        self.write_memory(addr, 1, instructions, len(instructions), raw=True)
        return

    def save_state(
        self,
        silent: bool = False,
        dirname: Optional[str] = None,
        overwrite: bool = False,
        specified_memory: Optional[Any] = None,
        specified_registers: Optional[Any] = None,
    ) -> Tuple[bool, Optional[Any]]:

        if not silent:
            hal_log.debug("#######################")
            hal_log.debug("HAL-SAVE")
            hal_log.debug("#######################")

        # Make tmp dir for all saves if it does not exist
        save_dir_path = "/tmp/hal_saves"
        os.makedirs(save_dir_path, exist_ok=True)

        # default dirname if none
        if dirname == None:
            dirname = "hal_save"

        # make directory.
        # Should rm previos dir (if exists) if overwrite=True
        # else - should make new dir with `dirname`#
        files = os.listdir(save_dir_path)
        if overwrite:
            save_dir_path = os.path.join(save_dir_path, dirname)
            if dirname in files:
                os.shutil.rmtree(save_dir_path)
        else:
            dirname = dirname + "%d"
            num = 0
            while (dirname % num) in files:
                num += 1
            save_dir_path = os.path.join(save_dir_path, dirname % num)
        os.makedirs(save_dir_path, exist_ok=False)

        # Change cwd, save old to switch back
        cwd = os.getcwd()
        os.chdir(save_dir_path)

        save_info = {}
        save_info["specified_memory"] = os.shutil.copy.copy(specified_memory)
        save_info["specified_registers"] = os.shutil.copy.copy(
            specified_registers
        )
        save_info["memory_map"] = {}
        save_info["register_map"] = {}

        # use specified lists if there, otherwise use defaults of everything (except unknown/unknown memory)
        if specified_memory == None:
            default_skipped_memory = {"unknown"}
            # TODO: Use this commented code instead of the line above, think it might fix errors
            # to_skip_memory = []
            # memories = set(self.avatar.config.memories.keys())
            # for mem in memories:
            #     if mem_config.emulate is not None: #not sure if this should be None or False, would need to test
            #         to_skip_memory.append(mem)
            # specified_memory = list(memories - to_skip_memory) #not sure if this has to change a little syntax wise either

            specified_memory = list(
                set(self.avatar.config.memories.keys())
                - default_skipped_memory
            )

        if specified_registers == None:
            specified_registers = self.regs._get_names()

        # For each memory region save its details and save the region using pmemsave
        for mem_name in specified_memory:
            mem_config = self.avatar.config.memories[mem_name]

            fname = "%s.bin" % mem_name
            pmem_save_params = {
                "val": mem_config.base_addr,
                "size": mem_config.size,
                "filename": os.path.join(save_dir_path, fname),
            }
            save_info["memory_map"][mem_name] = {
                "name": mem_config.name,
                "emulate": mem_config.emulate,
                "permissions": mem_config.permissions,
                "qemu_name": mem_config.qemu_name,
                "base_addr": mem_config.base_addr,
                "size": mem_config.size,
                "filename": fname,
            }

            if not silent:
                hal_log.debug("Saving" + str(pmem_save_params))
            try:
                self.protocols.monitor.execute_command(
                    "pmemsave", pmem_save_params
                )
            except Exception as e:
                if not silent:
                    hal_log.debug("got exception::::\n" + str(e))

        # Add registers to the info
        for reg_name in specified_registers:

            save_info["register_map"][reg_name] = self.read_register(reg_name)
        # Save the info
        with open(
            os.path.join(save_dir_path, "save_info.yaml"), "w"
        ) as outfile:
            yaml.dump(save_info, outfile)

        if not silent:
            hal_log.debug("Saved State to %s" % save_dir_path)

        os.chdir(cwd)
        return True, None
