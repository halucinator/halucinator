"""Tests for halucinator.bp_handlers.vxworks.tasks"""
import os
from unittest import mock

import pytest

from halucinator.bp_handlers.vxworks.tasks import Tasks, BColors


class TestBColors:
    def test_colors_exist(self):
        assert '\033[' in BColors.OKBLUE
        assert '\033[' in BColors.OKGREEN
        assert '\033[' in BColors.WARNING
        assert '\033[' in BColors.FAIL
        assert BColors.ENDC == '\033[0m'
        assert '\033[' in BColors.BOLD


class TestTasks:
    def test_init(self):
        t = Tasks()
        assert t.task_spawn_file == 'taskSpawn.log'
        assert t.task_name_counter == 0

    def test_register_handler_with_task_spawn_file(self, qemu, tmp_path):
        t = Tasks()
        qemu.avatar.output_directory = str(tmp_path)

        t.register_handler(qemu, 0x1000, 'log_taskSpawn',
                           task_spawn_file='test_spawn.log')

        assert t.task_spawn_file == 'test_spawn.log'
        outfile = tmp_path / 'test_spawn.log'
        assert outfile.exists()
        content = outfile.read_text()
        assert 'Name' in content
        assert 'Priority' in content

    def test_register_handler_no_file(self, qemu):
        t = Tasks()
        t.register_handler(qemu, 0x1000, 'log_taskSpawn')
        assert t.task_spawn_file == 'taskSpawn.log'

    def test_log_task_spawn_with_name(self, qemu, tmp_path):
        t = Tasks()
        qemu.avatar.output_directory = str(tmp_path)
        t.task_spawn_file = 'taskSpawn.log'

        # Create the output file first
        outfile = tmp_path / 'taskSpawn.log'
        outfile.write_text('')

        # get_arg(0) = task name ptr (nonzero)
        def get_arg_side_effect(n):
            vals = {
                0: 0x5000,   # task_name_ptr (nonzero -> read name)
                1: 100,      # priority
                2: 0x01,     # options
                3: 0x1000,   # stack size
                4: 0x8000,   # entry addr
            }
            return vals.get(n, 0x100 + n)
        qemu.get_arg = mock.Mock(side_effect=get_arg_side_effect)
        qemu.read_string = mock.Mock(return_value="tMyTask")
        qemu.regs.lr = 0x08001000

        result = t.log_task_spawn(qemu, 0x1000)

        assert result == (False, None)
        content = outfile.read_text()
        assert "tMyTask" in content

    def test_log_task_spawn_null_name(self, qemu, tmp_path):
        t = Tasks()
        qemu.avatar.output_directory = str(tmp_path)
        t.task_spawn_file = 'taskSpawn.log'

        outfile = tmp_path / 'taskSpawn.log'
        outfile.write_text('')

        # get_arg(0) = 0 (null task name ptr)
        def get_arg_side_effect(n):
            if n == 0:
                return 0
            return 0x100 + n
        qemu.get_arg = mock.Mock(side_effect=get_arg_side_effect)
        qemu.regs.lr = 0x08001000

        result = t.log_task_spawn(qemu, 0x1000)

        assert result == (False, None)
        assert t.task_name_counter == 1
        content = outfile.read_text()
        assert "t0" in content

    def test_log_task_spawn_increments_counter(self, qemu, tmp_path):
        t = Tasks()
        qemu.avatar.output_directory = str(tmp_path)
        t.task_spawn_file = 'taskSpawn.log'

        outfile = tmp_path / 'taskSpawn.log'
        outfile.write_text('')

        def get_arg_side_effect(n):
            if n == 0:
                return 0
            return 0x100 + n
        qemu.get_arg = mock.Mock(side_effect=get_arg_side_effect)
        qemu.regs.lr = 0x08001000

        t.log_task_spawn(qemu, 0x1000)
        t.log_task_spawn(qemu, 0x1000)

        assert t.task_name_counter == 2
