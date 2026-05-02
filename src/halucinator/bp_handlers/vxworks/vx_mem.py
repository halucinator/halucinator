'''vxworks shared memory module'''
import logging

from halucinator.bp_handlers.bp_handler import BPHandler, bp_handler

log = logging.getLogger(__name__)
class BColors:
    '''For pretty coloring of messages'''
    OKBLUE = '\033[94m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'

class VxMem(BPHandler):
    '''VxMem'''

    @bp_handler(['vxMemProbe'])
    def vx_mem_probe(self, qemu, addr):
        '''vx_mem_probe'''
        adrs = qemu.get_arg(0)      #should be an address of where we are probing
        mode = qemu.get_arg(1)      #either a 0 (READ) or 1 (WRITE)
        length = qemu.get_arg(2)    #either 1, 2, 4, or 8 bytes
        p_val = qemu.get_arg(3)      #if mode is READ, then value read from adrs is copied to location pointed to by p_val
                                    #if mode is WRITE, then the we take the value from the location pointed to by p_val
                                    #and write to adrs
        log.info(BColors.OKGREEN+"VxMemProbe")
        log.info("vx_mem_probe")
        log.info("adrs: %s", adrs)
        log.info("mode: %s", mode)
        log.info("length: %s", length)
        log.info("pVal: %s", p_val)
        log.info(BColors.ENDC)

        if mode == 0: #READ
            try:
                read_value = qemu.read_memory(adrs, length, 1)
                qemu.write_memory(p_val, 1, read_value, length, raw=True)
            except Exception as err:
                log.warning(BColors.WARNING+"VxMemProbe READ")
                log.warning("%s, args: %s", err.message, err.args)
                log.warning(BColors.ENDC)
        elif mode == 1: #WRITE
            try:
                write_value = qemu.read_memory(p_val, length, 1)
                qemu.write_memory(adrs, 1, write_value, length, raw=True)
            except Exception as err:
                log.warning(BColors.WARNING+"VxMemProbe WRITE")
                log.warning("%s, args: %s", err.message, err.args)
                log.warning(BColors.ENDC)
        else:
            log.warning(BColors.WARNING+"VxMemProbe")
            log.warning("Error in vxMemProbe. Should have mode 0 or 1, has %s", mode)
            log.warning(BColors.ENDC)

        return True, 0 #0: OK