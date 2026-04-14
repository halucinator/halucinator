"""Tests for halucinator.bp_handlers.vxworks.scheduler"""
import os
from unittest import mock

import pytest

from halucinator.bp_handlers.vxworks.scheduler import Scheduler, BColors, print_task, WIND_TCB_LEN


class TestBColors:
    def test_is_enum(self):
        assert BColors.OKBLUE.value == "\033[94m"
        assert BColors.ENDC.value == "\033[0m"


class TestPrintTask:
    def test_print_task_returns_name(self):
        task = (
            0x1000,      # p_tcb
            "myTask",    # task_name
            10,          # priority
            0x01,        # options
            0x2000,      # p_stack_base
            0x1000,      # stack_size
            0x3000,      # p_stack_limit
            0,           # unknown1
            0x4000,      # entry_addr
            "entryFunc", # entry_sym_name
            0x5000,      # caller_addr
            "callerFunc" # caller_name
        )
        logger = mock.Mock()
        result = print_task(logger, task)
        assert result == "myTask"
        assert logger.debug.call_count > 0


class TestScheduler:
    @pytest.fixture(autouse=True)
    def setup_tmp_files(self, tmp_path):
        """Patch the file paths to use tmp_path."""
        self.task_filename = str(tmp_path / "task_switch_lines.yaml")
        self.qemu_filename = str(tmp_path / "qemu_asm.log")

    def _make_scheduler(self, tcb=0x8000, kernel_id=0):
        with mock.patch("builtins.open", mock.mock_open()):
            sched = Scheduler(tcb=tcb, kernel_id=kernel_id)
        sched.task_filename = self.task_filename
        sched.qemu_filename = self.qemu_filename
        # Create the files
        with open(self.task_filename, "w") as f:
            f.write("task_positions:\n")
        with open(self.qemu_filename, "w") as f:
            pass
        return sched

    def test_init(self):
        with mock.patch("builtins.open", mock.mock_open()):
            sched = Scheduler(tcb=0x8000)
        assert sched.tcb == 0x8000
        assert sched.kernel_id == 0
        assert sched.last_task is None
        assert sched.task_switchcount == 0
        assert hex(0) in sched.tasks

    def test_init_kernel_task_present(self):
        with mock.patch("builtins.open", mock.mock_open()):
            sched = Scheduler(tcb=0x8000, kernel_id=0x100)
        assert hex(0x100) in sched.tasks
        task = sched.tasks[hex(0x100)]
        assert task[1] == "kernelTask"

    def test_reschedule(self, qemu):
        sched = self._make_scheduler()
        result = sched.reschedule(qemu, 0x1000)
        assert result == (False, None)

    def test_wind_resume(self, qemu):
        sched = self._make_scheduler()
        result = sched.wind_resume(qemu, 0x1000)
        assert result == (False, None)

    def test_work_q_do_work(self, qemu):
        sched = self._make_scheduler()
        result = sched.work_q_do_work(qemu, 0x1000)
        assert result == (False, None)

    def test_task_destroy(self, qemu):
        sched = self._make_scheduler()
        qemu.read_memory = mock.Mock(side_effect=[
            0x9000,      # tid_ptr from tcb
            "taskName",  # read_string result via read_memory at tid_ptr + 0x34
            0,           # exit code at tid_ptr + 0x88
        ])
        qemu.read_string = mock.Mock(return_value="deadTask")

        result = sched.task_destroy(qemu, 0x1000)
        assert result == (False, None)

    def test_task_switch_new_task(self, qemu):
        sched = self._make_scheduler()
        sched.last_task = None

        # c_tcb = read from self.tcb
        # tcb = array of 0x21 entries from c_tcb
        tcb_data = [0] * 0x21
        tcb_data[int(0x34 / 0x4)] = 0xA000  # task name ptr
        tcb_data[int(0x3C / 0x4)] = 1       # Status
        tcb_data[int(0x40 / 0x4)] = 10      # Priority
        tcb_data[int(0x44 / 0x4)] = 10      # PriNormal
        tcb_data[int(0x48 / 0x4)] = 0       # priMutexCnt
        tcb_data[int(0x50 / 0x4)] = 0       # lockCnt
        tcb_data[int(0x74 / 0x4)] = 0x4000  # entry
        tcb_data[int(0x78 / 0x4)] = 0x5000  # pStackBase
        tcb_data[int(0x7C / 0x4)] = 0x6000  # pStackLimit
        tcb_data[int(0x80 / 0x4)] = 0x7000  # pStackEnd

        qemu.read_memory = mock.Mock(side_effect=[0x9000, tcb_data])
        qemu.read_string = mock.Mock(return_value="newTask")

        result = sched.task_switch(qemu, 0x1000)

        assert result == (False, None)
        assert sched.task_switchcount == 1
        assert sched.last_task == "newTask"

    def test_task_switch_same_task(self, qemu):
        sched = self._make_scheduler()
        sched.last_task = "sameTask"

        tcb_data = [0] * 0x21
        tcb_data[int(0x34 / 0x4)] = 0xA000

        qemu.read_memory = mock.Mock(side_effect=[0x9000, tcb_data])
        qemu.read_string = mock.Mock(return_value="sameTask")

        result = sched.task_switch(qemu, 0x1000)

        assert result == (False, None)
        assert sched.task_switchcount == 1

    def test_task_switch_v7(self, qemu):
        sched = self._make_scheduler()
        sched.last_task = None

        tcb_data = [0] * 0x21
        tcb_data[int((0x34 - 0x4) / 0x4)] = 0xA000

        qemu.read_memory = mock.Mock(side_effect=[0x9000, tcb_data])
        qemu.read_string = mock.Mock(return_value="v7Task")

        result = sched.task_switch_v7(qemu, 0x1000)

        assert result == (False, None)
        assert sched.last_task == "v7Task"

    def test_task_switch_v7_same_task(self, qemu):
        sched = self._make_scheduler()
        sched.last_task = "v7Task"

        tcb_data = [0] * 0x21
        tcb_data[int((0x34 - 0x4) / 0x4)] = 0xA000

        qemu.read_memory = mock.Mock(side_effect=[0x9000, tcb_data])
        qemu.read_string = mock.Mock(return_value="v7Task")

        result = sched.task_switch_v7(qemu, 0x1000)

        assert result == (False, None)
        # last_task unchanged
        assert sched.last_task == "v7Task"

    def test_log_task_initialize(self, qemu):
        sched = self._make_scheduler()

        def get_arg_side_effect(n):
            vals = {
                0: 0x1000,  # p_tcb
                1: 0x2000,  # task_name_ptr (nonzero)
                2: 10,      # priority
                3: 0x01,    # options
                4: 0x3000,  # p_stack_base
                5: 0x1000,  # stack_size
                6: 0x4000,  # p_stack_limit
                7: 0,       # unknown1
                8: 0x5000,  # entry_addr
            }
            return vals.get(n, 0x100 + n)
        qemu.get_arg = mock.Mock(side_effect=get_arg_side_effect)
        qemu.read_string = mock.Mock(return_value="initTask")
        qemu.read_memory = mock.Mock(return_value=0x9000)
        qemu.regs.lr = 0x08001000

        result = sched.log_task_initialize(qemu, 0x1000)

        assert result == (False, 0)
        assert hex(0x1000) in sched.tasks

    def test_log_task_initialize_null_name(self, qemu):
        sched = self._make_scheduler()

        def get_arg_side_effect(n):
            vals = {
                0: 0x1000,
                1: 0,       # null task_name_ptr
                2: 10,
                3: 0x01,
                4: 0x3000,
                5: 0x1000,
                6: 0x4000,
                7: 0,
                8: 0x5000,
            }
            return vals.get(n, 0x100 + n)
        qemu.get_arg = mock.Mock(side_effect=get_arg_side_effect)
        qemu.read_memory = mock.Mock(return_value=0x9000)
        qemu.regs.lr = 0x08001000

        result = sched.log_task_initialize(qemu, 0x1000)

        assert result == (False, 0)
        task = sched.tasks[hex(0x1000)]
        assert task[1] == "t0"
        assert sched.task_name_counter == 1

    def test_task_switch_in(self, qemu):
        sched = self._make_scheduler()
        sched.tasks[hex(0x9000)] = (
            0x9000, "inTask", 10, 0x01, 0x2000, 0x1000,
            0x3000, 0, 0x4000, "entryFunc", 0x5000, "callerFunc"
        )

        qemu.read_memory = mock.Mock(return_value=0x9000)

        result = sched.task_switch_in(qemu, 0x1000)
        assert result == (False, None)

    def test_task_switch_out(self, qemu):
        sched = self._make_scheduler()
        sched.tasks[hex(0x9000)] = (
            0x9000, "outTask", 10, 0x01, 0x2000, 0x1000,
            0x3000, 0, 0x4000, "entryFunc", 0x5000, "callerFunc"
        )

        qemu.read_memory = mock.Mock(return_value=0x9000)

        result = sched.task_switch_out(qemu, 0x1000)
        assert result == (False, None)

    def test_task_switch_out_type_error(self, qemu):
        sched = self._make_scheduler()
        # p_tcb not in tasks -> KeyError, but source catches TypeError
        qemu.read_memory = mock.Mock(return_value=0xBBBB)
        sched.tasks[hex(0xBBBB)] = None  # Will cause TypeError in print_task

        result = sched.task_switch_out(qemu, 0x1000)
        assert result == (False, None)

    def test_wind_tcb_len(self):
        assert WIND_TCB_LEN == 124
