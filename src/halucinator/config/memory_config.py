import logging
from halucinator import hal_log as hal_log_conf
import os
log = logging.getLogger(__name__)
hal_log = hal_log_conf.getHalLogger()

class HalMemConfig(object):
    '''
        Parses the memory portions of halucinator's config file
        and represents that data with some helper functions
    '''
    def __init__(self, name, config_filename, base_addr, size, 
                 permissions='rwx', file=None, emulate=None, 
                 qemu_name=None, properties=None, irq=None):
        '''
            Reads in config
        '''
        self.name = name
        self.config_file = config_filename # For reporting where problems are
        self.file = file
        self.size = size
        self.permissions = permissions
        self.emulate = emulate
        self.emulate_required = False
        self.base_addr = base_addr
        self.qemu_name = qemu_name
        self.irq_config = irq
        self.properties = properties

        if self.file != None:
            self.get_full_path()
        
    def get_full_path(self):
        '''
            This make the file used by a memory relative to the config file
            containing it
        '''
        base_dir = os.path.dirname(self.config_file)
        if base_dir != None and not os.path.isabs(self.file):
            self.file = os.path.join(base_dir, self.file)

    def overlaps(self, other_mem):
        '''
            Checks to see if this memory description overlaps with 
            another

            :param (HalMemConfig) other_mem:
        '''
        if  self.base_addr >= other_mem.base_addr and \
            self.base_addr < other_mem.base_addr+ other_mem.size:
            return True

        elif other_mem.base_addr >= self.base_addr and \
            other_mem.base_addr < self.base_addr+ self.size:
            return True
        return False

    def is_valid(self):
        valid = True
        if self.size %(4096) != 0:
            hal_log.error("Memory/Peripheral: has invalid size, must be multiple of 4kB\n\t%s" % self)
            valid = False

        if self.emulate_required and self.emulate is None:
            hal_log.error("Memory/Peripheral: requires emulate field\n\t%s" % self)
            valid = False
        return valid

    def __repr__(self):
        return "(%s){name:%s, base_addr:%#x, size:%#x, emulate:%s}" % \
          (self.config_file, self.name, self.base_addr, self.size, self.emulate)
