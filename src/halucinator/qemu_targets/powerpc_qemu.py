# Copyright 2020 National Technology & Engineering Solutions of Sandia, LLC (NTESS).
# Under the terms of Contract DE-NA0003525 with NTESS, the U.S. Government retains
# certain rights in this software.


import binascii
import logging
import os
import struct
import yaml
from collections import deque

from avatar2 import Avatar, QemuTarget
from capstone import *
from keystone.keystone_const import *
from unicorn import *
from unicorn.arm_const import *

from halucinator import hal_config, hal_log
from halucinator.bp_handlers import intercepts
from halucinator.bp_handlers.bp_handler import BPHandler

log = logging.getLogger(__name__)
hal_log = hal_log.getHalLogger()


class AllocedMemory():
    def __init__(self, target, base_addr, size):
        self.target = target
        self.base_addr = base_addr
        self.size = size
        self.in_use = True
        #TODO add ability to set watchpoint for bounds checking

    def zero(self):
        zeros = "\x00"* self.size
        self.target.write_memory(self.base_addr, 1, zeros, raw=True)

    def alloc_portion(self, size):
        if size < self.size:
            new_alloc = AllocedMemory(self.target, self.base_addr, size)
            self.base_addr += size
            self.size -= size
            return new_alloc, self
        elif size == self.size:
            self.in_use = True
            return self, None
        else:
            raise ValueError("Trying to alloc %i bytes from chuck of size %i" %(size, self.size))

    def merge(self, block):
        '''
            Merges blocks with this one
        '''
        self.size += block.size
        self.base_addr = self.base_addr if self.base_addr <= block.base_addr else block.base_addr

class PowerPCQemuTarget(QemuTarget):
    '''
        Implements a QEMU target that has function args for use with
        halucinator.  Enables read/writing and returning from
        functions in a calling convention aware manner
    '''
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.irq_base_addr = None
        self.avatar.load_plugin('assembler')
        self.avatar.load_plugin('disassembler')
        # self.init_halucinator_heap()
        self.calls_memory_blocks = {}  # Look up table of allocated memory
                                       # used to perform calls

    def read_string(self, addr, max_len=256):
        s = self.read_memory(addr, 1, max_len, raw=True)
        s = s.decode('latin-1')
        return s.split('\x00')[0]


    def dictify(self, ignore=None):
        if ignore is None:
            ignore = ['state', 'status', 'regs', 'protocols', 'log', 'avatar',
                      'alloced_memory', 'free_memory', 'calls_memory_blocks']
        super().dictify(ignore)

    # def init_halucinator_heap(self):
    #     '''
    #         Initializes the scratch memory in the target that halucinator
    #         can use.  This requires that a 'halucinator' memory region
    #         exists.
    #     '''

    #     for mem_name, mem_data in self.avatar.config.memories.items():
    #         if mem_name == 'halucinator':
    #             heap = AllocedMemory(self, mem_data.base_addr, mem_data.size)
    #             heap.in_use = False
    #             self.alloced_memory = set()
    #             self.free_memory = set()
    #             self.free_memory.add(heap)
    #             return

    #     raise ValueError("Memory region named 'halucinator required")

    def hal_alloc(self, size):

        if size % 4:
            size += 4 - (size % 4) # keep aligned on 4 byte boundary
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

    def hal_free(self, mem):
        mem.is_used = False
        self.alloced_memory.remove(mem)
        merged_block = None
        for free_block in self.free_memory:
            # See if previous block is contiguous with this one
            if free_block.base_addr + free_block.size ==  mem.base_addr:
                free_block.merge(mem)
                merged_block = free_block
                break
            elif mem.base_addr + mem.size == free_block.base_addr:
                free_block.merge(mem)
                merged_block = free_block
                break

        if merged_block is None:
            self.free_memory.add(mem)
        else:
            self.free_scratch_memory(merged_block)


    def get_arg(self, idx):
        '''
            Gets the value for a function argument (zero indexed)

            :param idx  The argument index to return
            :returns    Argument value
        '''
        if idx >= 0 and idx < 8:
            return self.read_register(f"r{idx+3}")
        elif idx >= 8:
            sp = self.read_register("sp")
            stack_addr = sp + (idx - 8) * 4
            return self.read_memory(stack_addr, 4, 1)
        else:
            raise ValueError("Invalid arg index")


    def set_arg(self, idx, value):
        '''
            Sets the value for a function argument (zero indexed)

            :param idx:    Arg Index to write
            :param value:  Value for argument
        '''
        
        if idx < 8:
            self.write_register((f"r{idx+3}"), value)
        else:
            raise NotImplementedError("Writing individual stack args not " 
            "implemented")
        

    def set_args(self, args):
        '''
            Sets the value for a function argument (zero indexed)

            :param args:  Iterable of args to set
        '''
        for idx, value in enumerate(args[0:8]):
            if idx < 8:
                self.write_register((f"r{idx+3}"), value)
            else:
                break

        sp = self.read_register("sp")
        for idx, value in enumerate(args[:7:-1]):
            sp -= 4
            self.write_memory(sp, 4, value)

        self.write_register('sp', sp)
        return sp

    def push_lr(self):
        sp = self.read_register('sp')
        sp -= 4
        self.write_memory(sp, 4,self.read_register('lr'))
        self.write_register('sp', sp)

    def get_ret_addr(self):
        '''
            Gets the return address for the function call

            :returns Return address of the function call
        '''
        return self.regs.lr

    def set_ret_addr(self, ret_addr):
        '''
            Sets the return address for the function call
            :param ret_addr Value for return address
        '''
        self.regs.lr = ret_addr

    def execute_return(self, ret_value):
        if ret_value != None:
            # Puts ret value in r3
            self.regs.r3 = ret_value & 0xFFFFFFFF #Truncate to 32 bits
        self.regs.pc = self.regs.lr


    def get_irq_base_addr(self):
        raise NotImplementedError
        
    def irq_set_qmp(self, irq_num=1):
        '''
            Set interrupt using qmp.
            DO NOT execute in context of a bp handler, use irq_set_bp instead

            :param irq_num:  The irq number to trigger
        '''
        raise NotImplementedError

    def irq_clear_qmp(self, irq_num=1):
        '''
            Clear interrupt using qmp.
            DO NOT execute in context of a bp handler, use irq_clear_bp

            :param irq_num:  The irq number to trigger
        '''
        raise NotImplementedError
        
    def irq_set_bp(self, irq_num=1):
        raise NotImplementedError
        
    def irq_clear_bp(self, irq_num):
        raise NotImplementedError
        
    def irq_pulse(self, irq_num=1, cpu=0):
        raise NotImplementedError
        
    def get_symbol_name(self, addr):
        """
        Get the symbol for an address

        :param addr:    The name of a symbol whose address is wanted
        :returns:         (Symbol name on success else None
        """

        return self.avatar.config.get_symbol_name(addr)

    def set_bp(self, addr, handler_cls, handler, run_once=False):
        '''
            Adds a break point setting the class and method to handler it.

            :param addr:    Address of break point
            :param handler_cls:   Instance or import string for BPHandler class that
                            has handler for this bp
            :param handler: String identifing the method in handler_class to
                            handle the bp (ie. value in @bp_handler decorator)
            :param run_once:  Bool, BP should only trigger once
        '''
        if type(handler_cls) == str:
            cls_name = handler_cls
        else:
            cls_name = type(handler_cls).__module__ + "." + type(handler_cls).__qualname__
        config = {'cls': cls_name, 'run_once': run_once, 'function': handler,
                    'addr': addr}
        intercept_config = hal_config.HalInterceptConfig(__file__, **config)
        intercepts.register_bp_handler(self, intercept_config)

    def call_varg(self, ret_bp_handler, callee, *args):
        raise NotImplementedError("Calling Varg functions not supported")

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

            instrs.append(struct.pack('<I', addr))  # Address of callee
            instrs.append(self.assemble("mov pc, lr"))  # Return

            offset = len(b''.join(instrs)) #PC is two instructions ahead so need to calc offset
                                          # two instructions before its execution
            instrs.append(self.assemble("pop {lr}"))  # Retore LR

            # Clean up stack args
            if len(args) > 8:
                stack_var_size = (len(args) - 8) * 4
                instrs.append(self.assemble('add sp, sp, #%i'% stack_var_size))
                offset += 4

            instrs.append(self.assemble('blx lr'))    # Make Call
            instrs.append(self.assemble("ldr lr, [pc, #%i]" % offset)) # Load Callee Addr

            instructions = b''.join(instrs)

            mem = self.hal_alloc(len(instructions))

            bytes_written = 0
            while instrs:
                bytearr = instrs.pop()
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
        raise NotImplementedError
        # Need to determine if PPC can do PC relative load
        instrs = []
        instrs.append(self.assemble("l pc, 0 (pc),")) # 
        instrs.append(struct.pack('>I', branch_target))  # Address of callee
        instructions = b''.join(instrs)
        self.write_memory(addr, 1, instructions, len(instructions), raw=True)
        return

    def save_state(self,
            silent=False,
            dirname=None,
            overwrite=False,
            specified_memory=None,
            specified_registers=None):

        if not silent:
            hal_log.debug("#######################")
            hal_log.debug("HAL-SAVE")
            hal_log.debug("#######################")

        # Make tmp dir for all saves if it does not exist
        save_dir_path = '/tmp/hal_saves'
        os.makedirs(save_dir_path, exist_ok=True)

        # default dirname if none
        if dirname == None:
            dirname = 'hal_save'

        # make directory.
        # Should rm previos dir (if exists) if overwrite=True
        # else - should make new dir with `dirname`#
        files = os.listdir(save_dir_path)
        if overwrite:
            save_dir_path = os.path.join(save_dir_path, dirname)
            if dirname in files:
                os.shutil.rmtree(save_dir_path)
        else:
            dirname = dirname+'%d'
            num = 0
            while (dirname % num) in files:
                num+=1
            save_dir_path = os.path.join(save_dir_path, dirname % num)
        os.makedirs(save_dir_path, exist_ok=False)

        # Change cwd, save old to switch back
        cwd = os.getcwd()
        os.chdir(save_dir_path)

        save_info = {}
        save_info['specified_memory'] = os.shutil.copy.copy(specified_memory)
        save_info['specified_registers'] = os.shutil.copy.copy(specified_registers)
        save_info['memory_map'] = {}
        save_info['register_map'] = {}

        # use specified lists if there, otherwise use defaults of everything (except unknown/unknown memory)
        if specified_memory == None:
            default_skipped_memory = {'unknown'}
            # TODO: Use this commented code instead of the line above, think it might fix errors
            # to_skip_memory = []
            # memories = set(self.avatar.config.memories.keys())
            # for mem in memories:
            #     if mem_config.emulate is not None: #not sure if this should be None or False, would need to test
            #         to_skip_memory.append(mem)
            # specified_memory = list(memories - to_skip_memory) #not sure if this has to change a little syntax wise either

            specified_memory = list(set(self.avatar.config.memories.keys())-default_skipped_memory)

        if specified_registers == None:
            specified_registers = self.regs._get_names()

        # For each memory region save its details and save the region using pmemsave
        for mem_name in specified_memory:
            mem_config = self.avatar.config.memories[mem_name]

            fname = '%s.bin' % mem_name
            pmem_save_params = {
                'val': mem_config.base_addr,
                'size': mem_config.size,
                'filename': os.path.join(save_dir_path, fname)
            }
            save_info['memory_map'][mem_name] = {
                'name': mem_config.name,
                'emulate': mem_config.emulate,
                'permissions': mem_config.permissions,
                'qemu_name': mem_config.qemu_name,
                'base_addr': mem_config.base_addr,
                'size': mem_config.size,
                'filename': fname
            }

            if not silent:
                hal_log.debug("Saving" + str(pmem_save_params))
            try:
                self.protocols.monitor.execute_command('pmemsave', pmem_save_params)
            except Exception as e:
                if not silent:
                    hal_log.debug("got exception::::\n" + str(e))


        # Add registers to the info
        for reg_name in specified_registers:

            save_info['register_map'][reg_name] = self.read_register(reg_name)
        # Save the info
        with open(os.path.join(save_dir_path, 'save_info.yaml'), 'w') as outfile:
            yaml.dump(save_info, outfile)

        if not silent:
            hal_log.debug("Saved State to %s" % save_dir_path)

        os.chdir(cwd)
        return True, None

