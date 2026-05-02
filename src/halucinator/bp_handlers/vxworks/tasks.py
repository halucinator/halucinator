# Copyright 2021 National Technology & Engineering Solutions of Sandia, LLC
# (NTESS). Under the terms of Contract DE-NA0003525 with NTESS,
# the U.S. Government retains certain rights in this software.

'''tasks module'''
import logging
import os

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

class Tasks(BPHandler):
    '''
        Tasks
        This class can intercept common task methods and log their
        calling to a file.  Commonly used to get a list of tasks
        created
    '''
    def __init__(self):
        super().__init__()
        self.task_spawn_file = 'taskSpawn.log'
        self.task_name_counter = 0

    def register_handler(self, qemu, addr, func_name,
                         task_spawn_file=None):
        '''register_handler'''
        if task_spawn_file is not None:
            self.task_spawn_file = task_spawn_file
            outfile_name = os.path.join(qemu.avatar.output_directory, self.task_spawn_file)
            with open(outfile_name, 'w') as outfile:
                outfile.write("Name, Priority, Options, stackSize, entryAddr, ")
                outfile.write("entryName, callee_addr, callee_name,")
                args_str = ",".join(['arg %i' %i for i in range(10)])
                outfile.write(args_str)
                outfile.write('\n')

        return super().register_handler(qemu, addr, func_name)

    @bp_handler(['log_taskSpawn'])
    def log_task_spawn(self, qemu, bp_addr):
        '''
            Intercepted function with parameters
            int taskSpawn
                (
                char *  name,      /* name of new task (stored at pStackBase) */
                int     priority,  /* priority of new task */
                int     options,   /* task option word */
                int     stackSize, /* size (bytes) of stack needed plus name */
                FUNCPTR entryPt,   /* entry point of new task */
                int     arg1-10,      /* 1st of 10 req'd task args to pass to func */
                )
        '''
        log.debug( "-------------------VxWorks TaskSpawn-------------------")
        task_name_ptr = qemu.get_arg(0)
        if task_name_ptr != 0:
            task_name = qemu.read_string(task_name_ptr)
        else:
            task_name = "t%i" % self.task_name_counter
            self.task_name_counter += 1

        priority = qemu.get_arg(1)
        options = qemu.get_arg(2)
        stack_size = qemu.get_arg(3)
        entry_addr = qemu.get_arg(4)
        entry_sym_name = qemu.avatar.config.get_symbol_name(entry_addr)
        caller_addr = qemu.regs.lr
        caller_name = qemu.avatar.config.get_symbol_name(caller_addr)
        log.debug(task_name)
        params = []
        params.append(str(task_name))
        params.append(str(priority))
        params.append(hex(options))
        params.append(hex(stack_size))
        params.append(hex(entry_addr))
        params.append(str(entry_sym_name))
        params.append(hex(caller_addr))
        params.append(caller_name)
        for i in range(10):
            params.append(hex(qemu.get_arg(i + 5)))

        outfile_name = os.path.join(qemu.avatar.output_directory, self.task_spawn_file)
        with open(outfile_name, 'a') as outfile:
            outfile.write(",".join(params))
            outfile.write("\n")
        return False, None

# Library Calls
    # taskResume
    # taskActivate (just calls taskResume)
    # taskTcb
    # taskLock
    # taskUnlock

# Called from Library Calls:
    # taskRegsInit
    # taskArgsSet
    # taskHookInit
    # taskDeleteHookAdd
    # taskCreateLibInit
    # taskLibInit
    # taskStackAllot
    # taskInitialize
    # taskInitExcStk
    # taskOpenInit


# taskActivate( )
#     NAME
#     taskActivate( ) - activate a task that has been initialized

#     SYNOPSIS
#     STATUS taskActivate
#         (
#         int tid /* task ID of task to activate */
#         )
#     DESCRIPTION
#     This routine activates tasks created by taskInit( ). Without activation, a task is ineligible for CPU allocation by the scheduler.

#     The tid (task ID) argument is simply the address of the WIND_TCB for the task (the taskInit( ) pTcb argument), cast to an integer:

#     tid = (int) pTcb;
#     The taskSpawn( ) routine is built from taskActivate( ) and taskInit( ). Tasks created by taskSpawn( ) do not require explicit task activation.
#     RETURNS
#     OK, or ERROR if the task cannot be activated.



