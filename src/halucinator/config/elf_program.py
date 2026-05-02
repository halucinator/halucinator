from elftools.elf.elffile import ELFFile
from halucinator.config.memory_config import HalMemConfig
from halucinator.config.symbols_config import HalSymbolConfig
from halucinator import hal_log as hal_log_conf
import os
import subprocess
import logging
import math
import importlib


log = logging.getLogger(__name__)
hal_log = hal_log_conf.getHalLogger()

class ELFProgram(object):
    '''
        Handles parsing the elf_program section of the halucinator
        config file.

        It must be run after memory sections are parsed
    '''
    def __init__(self, config_filename, config, hal_config):
        self.config_file = config_filename
        self.config = config
        self.hal_config = hal_config
        self._parse_config(config)
        
        if self.build is not None:
            self.run_build_cmd()

        self.add_symbols()
        # Just easier to add them manually
        # self.add_memories_configs()  

    def _parse_config(self, config):
        '''
            Validates the elf program config and applies defaults

            :param config (dict):  ELF program dict
        '''
        errs = []
        self.name = config['name'] if 'name' in config else None
        self.build = config['build'] if 'build' in config else None
        self.execute_before = config['execute_before'] if 'execute_before' in config else True
        self.elf_filename = config['elf']
        self.elf_module_relative = config['elf_module_relative'] if 'elf_module_relative' in config else None
        self._intercepts = config['intercepts'] if 'intercepts' in config else []
        errs.extend(self._validate_intercepts())
            
        self.elf_filename = self.get_fullpath(self.elf_filename, 
                                              self.config_file, 
                                              self.elf_module_relative)
        self.exit_function = config['exit_function'] if 'exit_function' in config else 'exit'
        self.exit_to = None  # set with machine entry_point if this is to execute first
        return errs

    def _validate_intercepts(self):
        '''
            Validate that the format of the intercept has required fields
        '''
        errs = []
        if self._intercepts:
            for i in self._intercepts:
                if 'handler' not in i:
                    errs.append(f"`symbol` or `addr` must be in intercept: {i}\n")
                if 'symbol' not in i or 'addr' not in i:
                    errs.append(f"`symbol` or `addr` must be in intercept: {i}\n")
        log.debug("".join(errs))  #TODO remove and integrate with config error process
        return errs

    def get_fullpath(self, file_str, config_path, module_str=None ):
        '''
            Gets the full path for a file.  If file_str is a full path
            it just returns it, else if module_str is specified 
            returns a full path relative to the module, else returns a 
            full path relative to the config_path.

        '''
        if os.path.isabs(file_str):
            return file_str
        elif module_str is not None:
            module = importlib.import_module(module_str)
            return os.path.join(module.__path__, file_str)
        else:
            if (os.path.isdir(config_path)):
                base_dir = config_path
            else:
                base_dir = os.path.dirname(config_path)
            return os.path.join(base_dir, file_str)

    def run_build_cmd(self):
        '''
            Runs the program build command 
            :param path(str):  Path to execute command from. 
                               Either full path, make relative to config file
            :param cmd(str):  Command to execute
            :param halucinator_relative(bool): If path is prepended with path to halucinator module
        '''
        log.debug(f"Build is {self.build}")
        if self.build is not None:
            #If build specified execute it
            path = self.get_fullpath('', self.build['dir'], self.build['module_relative'])
            log.info(f"Building program with: {path}/{self.build['cmd']}")
            try:
                result = subprocess.run(self.build['cmd'], 
                                        cwd=path,
                                        check=True)
            except subprocess.CalledProcessError:
                log.error("Error building elf program\n")
                exit(-1)

    def add_memories_configs(self):
        '''
            Adds the memories to support running this elf to
            the memory config used to build the qemu_machine
        '''
        memories = []
        with open (self.elf_filename, 'rb') as infile:
            elf = ELFFile(infile)
            for seg_num in range(elf.num_segments()):
                seg = elf.get_segment(seg_num)
                base_addr = seg.header.p_paddr 
                size = seg.header.p_memsz
                align = seg.header.p_align
                mem_size = (math.ceil(size / align) * align)
                end_addr = base_addr + mem_size
                base_addr &= ~(0xFFF)  # Align to 4K boundary
                in_other_seg = False
                for mem in memories:
                    if end_addr < mem['base_addr'] or \
                        base_addr > (mem['base_addr'] + mem['size']):
                        continue # No overlap
                    else:  # There is an overlap
                        log.debug(f"Overlap Found {hex(base_addr)}:{hex(end_addr)} and {hex(mem['base_addr'])}:{hex(mem['base_addr']+mem['size'])}")
                        lower_bound = min(base_addr, mem['base_addr'])
                        upper_bound = max(end_addr, mem['base_addr']+ mem['size'])
                        mem['base_addr'] = lower_bound
                        mem['size'] = upper_bound - lower_bound
                        mem['permissions'] |= seg.header.p_flags
                        log.debug(f"New Bounds {hex(lower_bound)}:{hex(upper_bound)}")
                        in_other_seg = True
                        break

                if not in_other_seg:
                    log.debug(f"Adding segment: {hex(base_addr)} size:{hex(mem_size)}")
                    memories.append({'base_addr': base_addr, 
                                     'size': mem_size, 
                                     'permissions':seg.header.p_flags})
        for idx, mem in enumerate(memories):
            per = mem['permissions']
            per_str = 'r' if per & 0x4 else '-' + \
                      'w' if per & 0x2 else '-' + \
                      'x' if per & 0x1 else '-'
            name = f"{self.name}_mem_{idx}"
            mem_config = HalMemConfig(name, self.elf_filename, 
                         mem['base_addr'], mem['size'], per_str)

            for mem in self.hal_config.memories.values():
                if mem.overlaps(mem_config):
                    hal_log.critical(f"ELF Program conflicts with memory")
                    hal_log.critical(f"{mem_config}")
                    hal_log.critical(f"{mem}")
                    exit(-1)
            log.debug(f"Adding memory to machine {name}:{mem_config}")
            self.hal_config.memories[name] = mem_config

    def set_intercepts(self,qemu_target):
        '''
            Rewrites the program under test (put) to use the functions
            provided by this elf.

            :param hal_config(HalucinatorConfig):  Halucinator Config
            :param qemu_target(avatar.QemuTarget): Avatar Qemu Target
        '''
        log.debug("In Elf Program setting symbols")
        no_error = True
        for intercept in self._intercepts:
            handler_addr = self.get_function_addr(intercept['handler'])
            if handler_addr is None:
                hal_log.error(f"No symbol for {intercept['handler']} in {self.elf_filename}")
                no_error = False
                continue
            try:
                put_addr = intercept['addr']
            except KeyError:
                put_addr = self.hal_config.get_addr_for_symbol(intercept['symbol'])
            if put_addr is None:
                hal_log.error(f"No addr/symbol for {intercept} in {self.elf_filename}")
                no_error = False
                continue
            if 'symbol' in intercept:
                symbol = intercept['symbol'] if 'symbol' in intercept else None
                hal_log.info(f"Setting C intercept Handler: {intercept['handler']} ({hex(handler_addr)}) intercepting: {symbol}({hex(put_addr)})")
            qemu_target.write_branch(put_addr, handler_addr)

        return no_error

    def load_segments(self, qemu_target):
        '''
            Load the elf segments to memory

            :param qemu_target(avatar.QemuTarget): The QEMU target to load into
        '''
        with open (self.elf_filename, 'rb') as infile:
            elf = ELFFile(infile)
            for seg_num in range(elf.num_segments()):
                seg = elf.get_segment(seg_num)
                base_addr = seg.header.p_paddr 
                if seg.header.p_type == 'PT_LOAD':
                    hal_log.debug(f"Loading segment {seg.header}")
                    qemu_target.write_memory(base_addr, 1, seg.data(), raw=True)

    def initialize(self, qemu_target):
        '''
            Initializes the elf program in the target after 
            it is created.

            :param qemu_target:
        '''
        self.set_intercepts(qemu_target)
        # Using QEMU Loader for the elf program
        # self.load_segments(qemu_target)

        if self.exit_to is not None:
            # Rewrite_exit function to call exit_to addr
            exit_func_addr = self.get_function_addr(self.exit_function)
            qemu_target.write_branch(exit_func_addr, self.exit_to)

    def get_sym_name(self, name):
        '''
            Makes the symbol name unique for this program

            :param name: symbol name
        '''
        return f"${self.name}${name}"

    def get_function_addr(self, func_name):
        '''
            returns the address of a function in this elf file
        '''
        name = self.get_sym_name(func_name)
        return self.hal_config.get_addr_for_symbol(name)

    def add_symbols(self):
        '''
            Adds the symbols in the elf file to halucinators
            config list of symbols so they can be looked up
        '''
        with open(self.elf_filename, 'rb') as infile:
            elf = ELFFile(infile)
            symtab = elf.get_section_by_name('.symtab')
            log.debug(f"Adding {symtab.num_symbols} from {self.elf_filename}")
            for sym in symtab.iter_symbols():
                addr = sym['st_value']
                size = sym['st_size']
                sym_name = self.get_sym_name(sym.name)
                sym = HalSymbolConfig(self.elf_filename, name=sym_name, addr=addr, size=size)
                
                self.hal_config.symbols.append(sym)
    
    def get_entry_addr(self):
        '''
            Gets the address of the entry point for the elf file
        '''
        with open (self.elf_filename, 'rb') as infile:
            elf = ELFFile(infile)
            return elf.header['e_entry']
