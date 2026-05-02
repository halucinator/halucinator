# Copyright 2021 National Technology & Engineering Solutions of Sandia, LLC 
# (NTESS). Under the terms of Contract DE-NA0003525 with NTESS, 
# the U.S. Government retains certain rights in this software.

from ...bp_handler import BPHandler, bp_handler
import logging
from .... import hal_log
import sys
from collections import defaultdict

log = logging.getLogger(__name__)
hal_log = hal_log.getHalLogger()

class ParameterTest(BPHandler):
    '''
        This tests that parameters are passed to functions correctly and 
        that the stack is setup properly when using Halucinator's call 
        functionality 

        How it works, 
        run_test is intercepted and a call to four_parameters is inserted
        It then chains to  call_five_parameters, which in turn chains to 
        call_ten_parameters.  Execution should then continue.

        Each function called function writes its parameters using write_int
        which is intercepted to check parameters.
    '''
    def __init__(self):
        # Used to save stack pointers to ensure clean up is done properly
        self.four_sp = None
        self.five_sp = None
        self.ten_sp = None
        self.states = defaultdict(list)
        self.state = 'start'
        self.passed = True

    @bp_handler(['write_int'])
    def write_int(self, qemu, addr):
        i = qemu.get_arg(0)
        self.states[self.state].append(i)
        log.info("Write Int: %i"% i)
        return True, None

    @bp_handler(['run_test'])
    def run_test(self, qemu, addr):
        '''
            This will call memory copy the first time
        '''
        
        self.four_sp = qemu.regs.sp
        log.info("Calling four_parameters: SP is %#x"%self.four_sp)
        self.state = 'four'
        return qemu.call('four_parameters',[1,2,3,4], self, 'call_five')


    @bp_handler(['call_five'])
    def call_five(self, qemu, addr):
        '''
            call_five
        '''
        self.five_sp = qemu.regs.sp
        if self.four_sp != self.five_sp:
            log.error("Call Four Corrupted SP: In %#x, Out %#x"%(self.four_sp, self.five_sp))
            self.passed = False
        log.info("Calling five_parameters: SP is %#x" % self.five_sp)
        self.state = 'five'
        return qemu.call('five_parameters',[1,2,3,4,5], self, 'call_ten')
        

    @bp_handler(['call_ten'])
    def call_ten(self, qemu, addr):
        '''
            call_ten
        '''
        self.ten_sp = qemu.regs.sp
        if self.ten_sp != self.five_sp:
            log.error("Call Five Corrupted SP: In %#x, Out %#x"%(self.five_sp, self.ten_sp))
            self.passed = False
        log.info("Calling ten_parameters: SP is %#x" % self.ten_sp)
        self.state = 'ten'
        return qemu.call('ten_parameters',range(1,11), self, 'end_ten')

    @bp_handler(['end_ten'])
    def end_ten(self, qemu, addr):
        log.info("Running Ten End")
        if self.ten_sp != qemu.regs.sp:
            log.error("Call Ten Corrupted SP: In %#x, Out %#x"%(self.ten_sp, qemu.regs.sp))
            self.passed = False
        # import IPython; IPython.embed()
        # TODO: This doesn't work because GDB is used to set PC to address of 
        # break point on end test and bp_doesn't get hit.
        return True, None

    @bp_handler(['exit'])
    def end_test(self, qemu, addr):
        '''
            Make sure we executed everyting like we expected
        '''
        results = {'four':[1,2,3,4],
                    'five':[1,2,3,4,5],
                    'ten': list(range(1,11))}
        
        for key, l in results.items():
            if l != self.states[key]:
                log.error("Unexpected Result for %s: %s vs %s"%(key, l, self.states[key]))
                self.passed = False

        avatar = qemu.avatar
        avatar.stop()
        avatar.shutdown()
        # periph_server.stop()
        if self.passed:
            print("RESULT: PASSED")
            sys.exit(0)         
        else:
            print("RESULT: FAILED")
            sys.exit(-1)
            



