# Copyright 2021 National Technology & Engineering Solutions of Sandia, LLC
# (NTESS). Under the terms of Contract DE-NA0003525 with NTESS,
# the U.S. Government retains certain rights in this software.

"""Scheduler module for VxWorks"""
import logging
import os
from enum import Enum
from halucinator.bp_handlers.bp_handler import BPHandler, bp_handler

log = logging.getLogger(__name__)

WIND_TCB_LEN = 124  # 496 bytes, 124 entries x 4 bytes


class BColors(Enum):
    """For pretty coloring of messages"""

    OKBLUE = "\033[94m"
    OKGREEN = "\033[92m"
    WARNING = "\033[93m"
    FAIL = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"


def print_task(local_log, task):
    """prints a task, assuming it went through the taskInitialize for intialization and logging"""
    (
        p_tcb,
        task_name,
        priority,
        options,
        p_stack_base,
        stack_size,
        p_stack_limit,
        unknown1,
        entry_addr,
        entry_sym_name,
        caller_addr,
        caller_name,
    ) = task
    local_log.debug("TASK:")
    local_log.debug("pTcb ptr: %s", hex(p_tcb))
    local_log.debug("Task Name: %s", task_name)
    local_log.debug("priority: %s", hex(priority))
    local_log.debug("options: %s", hex(options))
    local_log.debug("pStackBase: %s", hex(p_stack_base))
    local_log.debug("stack_size: %s", hex(stack_size))
    local_log.debug("pStackLimit: %s", hex(p_stack_limit))
    local_log.debug("unknown1: %s", hex(unknown1))
    local_log.debug("entry_addr: %s", hex(entry_addr))
    local_log.debug("entry_sym_name: %s", entry_sym_name)
    local_log.debug("caller_addr: %s", hex(caller_addr))
    local_log.debug("caller_name: %s", caller_name)
    return task_name


class Scheduler(BPHandler):
    """Scheduler Class BP handler"""

    # pylint: disable=too-many-instance-attributes

    def __init__(self, tcb, kernel_id=0):
        """
        VxWorks Scheduler Functions instrumentation

        config file entry
        - class
          class_args: {tcb: (address of TCB)}
        """
        self.last_task = None
        self.tcb = tcb
        self.kernel_id = kernel_id
        self.tasks = {}
        self.task_name_counter = 0
        self.task_filename = os.path.abspath("tmp/HALucinator/task_switch_lines.yaml")
        self.qemu_filename = os.path.abspath("tmp/HALucinator/qemu_asm.log")
        self.tasks[hex(self.kernel_id)] = (
            self.kernel_id,
            "kernelTask",
            0,
            0,
            self.kernel_id,
            0x50000,
            0,
            0,
            0,
            0,
            0,
            0,
        )
        with open(self.task_filename, "w") as task_file:
            task_file.write("task_positions:\n")

        self.task_switchcount = 0
        self.resume_count = 0

    @bp_handler(["reschedule"])
    def reschedule(self, qemu, bp_addr):  # pylint: disable=unused-argument,no-self-use
        """reschedule"""
        log.debug("reschedule")
        return False, None

    @bp_handler(["windResume"])
    def wind_resume(self, qemu, bp_addr):  # pylint: disable=unused-argument,no-self-use
        """wind_resume"""
        log.debug("wind_resume")
        return False, None

    @bp_handler(["workQDoWork"])
    def work_q_do_work(
        self, qemu, bp_addr
    ):  # pylint: disable=unused-argument,no-self-use
        """work_q_do_work"""
        log.debug("work_q_do_work")
        return False, None

    @bp_handler(["taskDestroy"])
    def task_destroy(self, qemu, bp_addr):  # pylint: disable=unused-argument
        """task_destroy"""
        tid_ptr = qemu.read_memory(self.tcb, 4, 1)
        log.debug(BColors.FAIL)
        log.debug("TASK DESTROYED")
        log.debug(
            "Task Name: %s", qemu.read_string(qemu.read_memory(tid_ptr + 0x34, 4, 1))
        )
        log.debug("Exit Code: %s", hex(qemu.read_memory(tid_ptr + 0x88, 4, 1)))

        return False, None

    @bp_handler(["task_switch"])
    def task_switch(self, qemu, bp_addr):  # pylint: disable=unused-argument
        """task_switch"""
        c_tcb = qemu.read_memory(self.tcb, 4, 1)
        tcb = qemu.read_memory(c_tcb, 4, 0x21)
        offset_lookup = {
            "Status": tcb[int(0x3C / 0x4)],
            "Priority": tcb[int(0x40 / 0x4)],
            "PriNormal": tcb[int(0x44 / 0x4)],
            "priMutexCnt": tcb[int(0x48 / 0x4)],
            "lockCnt": tcb[int(0x50 / 0x4)],
            "entry": hex(tcb[int(0x74 / 0x4)]),
            "pStackBase": hex(tcb[int(0x78 / 0x4)]),
            "pStackLimit": hex(tcb[int(0x7C / 0x4)]),
            "pStackEnd": hex(tcb[int(0x80 / 0x4)]),
        }
        self.task_switchcount += 1
        if self.last_task == qemu.read_string(tcb[int(0x34 / 0x4)]):
            pass
        else:
            self.last_task = qemu.read_string(tcb[int(0x34 / 0x4)])
            log.debug(BColors.OKBLUE)
            log.debug("TASK SWITCH %i", self.task_switchcount)
            log.debug("Task Name: %s", qemu.read_string(tcb[int(0x34 / 0x4)]))
            for key, value in offset_lookup.items():
                log.debug(" %s: %s", key, value)
            log.debug(BColors.ENDC)
        return False, None

    @bp_handler(["task_switch_v7"])
    def task_switch_v7(self, qemu, bp_addr):  # pylint: disable=unused-argument
        """task_switch for arm v7"""
        c_tcb = qemu.read_memory(self.tcb, 4, 1)
        tcb = qemu.read_memory(c_tcb, 4, 0x21)
        offset_lookup = {
            "Status": tcb[int((0x3C - 0x4) / 0x4)],
            "Priority": tcb[int((0x40 - 0x4) / 0x4)],
            "PriNormal": tcb[int((0x44 - 0x4) / 0x4)],
            "priMutexCnt": tcb[int((0x48 - 0x4) / 0x4)],
            "lockCnt": tcb[int((0x50 - 0x4) / 0x4)],
            "entry": hex(tcb[int((0x74 - 0x4) / 0x4)]),
            "pStackBase": hex(tcb[int((0x78 - 0x4) / 0x4)]),
            "pStackLimit": hex(tcb[int((0x7C - 0x4) / 0x4)]),
            "pStackEnd": hex(tcb[int((0x80 - 0x4) / 0x4)]),
        }
        if self.last_task == qemu.read_string(tcb[int((0x34 - 0x4) / 0x4)]):
            pass
        else:
            self.last_task = qemu.read_string(tcb[int((0x34 - 0x4) / 0x4)])
            log.debug(BColors.OKBLUE)
            log.debug("TASK SWITCH")
            log.debug("Task Name: %s", qemu.read_string(tcb[int((0x34 - 0x4) / 0x4)]))
            for key, value in offset_lookup.items():
                log.debug(" %s: %s", key, value)
            log.debug(BColors.ENDC)
        return False, None

    @bp_handler(
        ["task_initialize"]
    )  # don't confuse with taskInit, this has 2 extra params
    def log_task_initialize(
        self, qemu, bp_addr
    ):  # pylint: disable=unused-argument,too-many-locals
        """
        Intercepted function with parameters
        STATUS taskInit
            (
            FAST WIND_TCB *pTcb,        /* address of new task's TCB */
            char          *name,        /* name of new task (stored at pStackBase) */
            int           priority,     /* priority of new task */
            int           options,      /* task option word */
            char          *pStackBase,  /* base of new task's stack */
            int           stackSize,    /* size (bytes) of stack needed */
            char          *pStackEnd,
            int unknown1, ()
            FUNCPTR       entryPt,      /* entry point of new task */
            int           arg1,         /* first of ten task args to pass to func */
            int           arg2,
            int           arg3,
            int           arg4,
            int           arg5,
            int           arg6,
            int           arg7,
            int           arg8,
            int           arg9,
            int           arg10
            )
        """
        log.debug("-------------------VxWorks TaskSpawn-------------------")
        p_tcb = qemu.get_arg(0)
        task_name_ptr = qemu.get_arg(1)
        if task_name_ptr != 0:
            task_name = qemu.read_string(task_name_ptr)
        else:
            task_name = f"t{self.task_name_counter}"
            self.task_name_counter += 1

        priority = qemu.get_arg(2)
        options = qemu.get_arg(3)
        p_stack_base = qemu.get_arg(4)
        stack_size = qemu.get_arg(5)
        p_stack_limit = qemu.get_arg(6)
        unknown1 = qemu.get_arg(7)
        entry_addr = qemu.get_arg(8)
        entry_sym_name = qemu.avatar.config.get_symbol_name(entry_addr)
        caller_addr = qemu.regs.lr
        caller_name = qemu.avatar.config.get_symbol_name(caller_addr)

        self.tasks[hex(p_tcb)] = (
            p_tcb,
            task_name,
            priority,
            options,
            p_stack_base,
            stack_size,
            p_stack_limit,
            unknown1,
            entry_addr,
            entry_sym_name,
            caller_addr,
            caller_name,
        )
        log.debug(BColors.OKGREEN)
        log.debug("TASK INITIALIZE")
        log.debug("tcb: %s", hex(qemu.read_memory(self.tcb, 4, 1)))
        print_task(log, self.tasks[hex(p_tcb)])
        log.debug(BColors.ENDC)

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
            params.append(hex(qemu.get_arg(i + 9)))

        return False, 0

    @bp_handler(["task_switch_in"])
    def task_switch_in(self, qemu, bp_addr):  # pylint: disable=unused-argument
        """task_switch_in"""
        p_tcb = qemu.read_memory(self.tcb, 4, 1)
        # tcb = qemu.read_memory(p_tcb, 4, WIND_TCB_LEN)

        log.debug(BColors.OKBLUE)
        log.debug("CURRENT TASK BEING SWITCHED IN")
        log.debug("TCB: %s", hex(qemu.read_memory(self.tcb, 4, 1)))
        task_name = print_task(log, self.tasks[hex(p_tcb)])
        log.debug(BColors.ENDC)

        pos = 0
        with open(self.qemu_filename, "a") as qemu_log:
            pos = qemu_log.tell()
        with open(self.task_filename, "a") as task_log:
            task_log.write(f"  - position: {pos}\n    task_name: {task_name}\n")
        return False, None

    @bp_handler(["task_switch_out"])
    def task_switch_out(self, qemu, bp_addr):  # pylint: disable=unused-argument
        """
        BP handler used to log switching out of task
        """
        p_tcb = qemu.read_memory(self.tcb, 4, 1)
        try:
            log.debug(BColors.WARNING)
            log.debug("CURRENT TASK BEING SWITCHED OUT")
            log.debug("TCB: %s", hex(qemu.read_memory(self.tcb, 4, 1)))
            print_task(log, self.tasks[hex(p_tcb)])
            log.debug(BColors.ENDC)
        except TypeError:
            log.debug(BColors.ENDC)
            log.debug(BColors.FAIL)
            log.debug("Could not print task_switch_out, error!")
            log.debug(
                "Possible Root Task Addr: %s", hex(qemu.read_memory(self.tcb, 4, 1))
            )
            log.debug(BColors.ENDC)
        return False, None
