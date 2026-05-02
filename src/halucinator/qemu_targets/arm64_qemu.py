# Copyright 2021 National Technology & Engineering Solutions of Sandia, LLC
# (NTESS). Under the terms of Contract DE-NA0003525 with NTESS,
# the U.S. Government retains certain rights in this software.

from .arm_qemu import ARMQemuTarget

class ARM64QemuTarget(ARMQemuTarget):

    def hal_alloc(self, size):

        if size % 8:
            size += 8 - (size % 8) # keep aligned on 8 byte boundary
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

    def get_arg(self, idx):
        '''
            Gets the value for a function argument (zero indexed)

            :param idx  The argument index to return
            :returns    Argument value
        '''
        if idx >= 0 and idx < 8:
            return self.read_register("x%i" % idx)
        elif idx >= 8:
            sp = self.read_register("sp")
            stack_addr = sp + (idx-8) * 8
            return self.read_memory(stack_addr, 8, 1)
        else:
            raise ValueError("Invalid arg index")

    def set_arg(self, idx, value):
        """Set a single function argument by index (zero-indexed, registers r0–r3 then stack)."""
        if idx < 0:
            raise ValueError(f"Argument index must be non-negative, got {idx}")
        if idx < 4:
            self.write_register(f"r{idx}", value)
        else:
            sp = self.read_register("sp")
            self.write_memory(sp + (idx - 4) * 4, 4, value)

    def set_args(self, args):
        '''
            Sets the value for a function argument (zero indexed)

            :param args:  Iterable of args to set
        '''
        for idx, value in enumerate(args[0:8]):
            if idx < 8:
                self.write_register(("x%i" % idx), value)
            else:
                break

        sp = self.read_register("sp")
        for idx, value in enumerate(args[:7:-1]):
            sp -= 8
            self.write_memory(sp, 8, value)

        self.write_register('sp', sp)
        return sp

    def push_lr(self):
        sp = self.read_register('sp')
        sp -= 8
        self.write_memory(sp, 8,self.read_register('lr'))
        self.write_register('sp', sp)
    def execute_return(self, ret_value):

        if ret_value != None:
            # Puts ret value in r0
            self.regs.x0 = ret_value
        self.regs.pc = self.regs.x30

    def call(self, callee, args=None, bp_handler_cls=None, ret_bp_handler=None,
            debug=False):
        '''
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
        '''

        if type(callee) == int:
            addr = callee
        else:
            addr = self.avatar.config.get_addr_for_symbol(callee)

        if addr == None:
            raise ValueError("Making call to %s.  Address for not found for: %s"
                             % (callee,callee))

        key = (bp_handler_cls.__class__, ret_bp_handler, addr, len(args))
        self.push_lr()
        new_sp = self.set_args(args)

        # If first time seeing this inject instructions to execute
        if key not in self.calls_memory_blocks:
            instrs = deque()
            # Build instructions in reverse order so we know offset to end
            # Where we store the address of the function to be called

            instrs.append(struct.pack('<I', addr).decode('latin-1'))  # Address of callee
            instrs.append(self.assemble("mov pc, lr"))  # Return
            offset = len(''.join(instrs)) #PC is two instructions ahead so need to calc offset
                                          # two instructions before its execution
            instrs.append(self.assemble("pop {lr}"))  # Retore LR

            # Clean up stack args
            if len(args) > 8:
                stack_var_size = (len(args) - 8) * 8
                instrs.append(self.assemble('add sp, sp, #%i'% stack_var_size))
                offset += 8

            instrs.append(self.assemble('blx lr'))    # Make Call
            instrs.append(self.assemble("ldr lr, [pc, #%i]" % offset)) # Load Callee Addr
            # instrs.append(self.assemble("push {lr}")) # Have to Save before
            instructions = ''.join(instrs)
            instr_bytes = bytearray(instructions,'latin-1')
            mem = self.hal_alloc(len(instr_bytes))

            bytes_written = 0
            while instrs:
                bytearr = bytearray(instrs.pop(), 'latin-1')
                inst_addr = mem.base_addr + bytes_written
                self.write_memory(inst_addr, 1,
                                    bytearr, len(bytearr), raw=True)

                dis = self.disassemble(inst_addr)
                dis_str = dis[0].insn_name() + " " + dis[0].op_str

                log.debug("Injected %#x:  %s\t %s " % (
                    inst_addr,
                    binascii.hexlify(bytearr),
                    dis_str))

                # Set break point before this function returns so new BP handler
                # can do its stuff if set
                if len(instrs) == 1:  # last "intruction written is addr"
                    if bp_handler_cls is not None and ret_bp_handler is not None:
                        self.set_bp(inst_addr, bp_handler_cls, ret_bp_handler)
                bytes_written += len(bytearr)
        else:
            mem = self.calls_memory_blocks[key]

        if debug:
            self.set_bp(mem.base_addr, "halucinator.bp_handlers.generic.debug.IPythonShell", 'shell')

        self.regs.pc = mem.base_addr
        return False, None


    def write_branch(self, addr, branch_target, options=None):
        '''
            Places an absolute branch at address addr to
            branch_target

            :param addr(int): Address to write the branch code to
            :param branch_target: Address to branch too
        '''
        raise NotImplemented("Write branch not implemented")
