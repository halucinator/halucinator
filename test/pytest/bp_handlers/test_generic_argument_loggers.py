"""Tests for halucinator.bp_handlers.generic.argument_loggers module."""

from unittest import mock

import pytest

from halucinator.bp_handlers.generic.argument_loggers import ArgumentLogger


@pytest.fixture
def qemu():
    m = mock.Mock()
    m.regs = mock.Mock()
    return m


ADDR = 0x1000


class TestArgumentLogger:
    def test_init_no_filename(self):
        logger = ArgumentLogger()
        assert logger.fd is None

    def test_init_with_filename(self, tmp_path):
        fname = str(tmp_path / "log.txt")
        logger = ArgumentLogger(filename=fname)
        assert logger.fd is not None
        logger.fd.close()

    def test_register_handler(self, qemu):
        logger = ArgumentLogger()
        result = logger.register_handler(
            qemu, ADDR, "my_func", num_args=3, log_ret_addr=True,
            intercept=True, ret_value=42, silent=False
        )
        assert ADDR in logger.loggers
        inner = logger.loggers[ADDR]
        assert inner.func_name == "my_func"
        assert inner.num_args == 3
        assert inner.log_caller is True
        assert inner.intercept is True
        assert inner.ret_value == 42
        assert inner.silent is False

    def test_log_handler_no_intercept(self, qemu):
        logger = ArgumentLogger()
        logger.register_handler(
            qemu, ADDR, "my_func", num_args=0,
            intercept=False, ret_value=None, silent=True
        )

        intercept, ret = logger.log_handler(qemu, ADDR)
        assert intercept is False
        assert ret is None

    def test_log_handler_with_intercept(self, qemu):
        logger = ArgumentLogger()
        logger.register_handler(
            qemu, ADDR, "my_func", num_args=0,
            intercept=True, ret_value=0xFF, silent=True
        )

        intercept, ret = logger.log_handler(qemu, ADDR)
        assert intercept is True
        assert ret == 0xFF

    def test_log_handler_not_silent_no_args(self, qemu):
        logger = ArgumentLogger()
        qemu.get_ret_addr.return_value = 0x2000
        logger.register_handler(
            qemu, ADDR, "my_func", num_args=0,
            log_ret_addr=True, intercept=False, ret_value=None, silent=False
        )

        intercept, ret = logger.log_handler(qemu, ADDR)
        assert intercept is False

    def test_log_handler_not_silent_with_args(self, qemu):
        logger = ArgumentLogger()
        qemu.get_arg.side_effect = [0x10, 0x20]
        qemu.get_ret_addr.return_value = 0x2000
        logger.register_handler(
            qemu, ADDR, "my_func", num_args=2,
            log_ret_addr=True, intercept=False, ret_value=None, silent=False
        )

        intercept, ret = logger.log_handler(qemu, ADDR)
        assert intercept is False

    def test_register_handler_default_args(self, qemu):
        logger = ArgumentLogger()
        logger.register_handler(qemu, ADDR, "default_func")
        inner = logger.loggers[ADDR]
        assert inner.num_args == 0
        assert inner.log_caller is True
        assert inner.intercept is False
        assert inner.ret_value is None
        assert inner.silent is False
