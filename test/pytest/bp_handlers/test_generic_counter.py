"""Tests for halucinator.bp_handlers.generic.counter module."""

from unittest import mock

import pytest

from halucinator.bp_handlers.generic.counter import Counter


@pytest.fixture
def qemu():
    m = mock.Mock()
    m.regs = mock.Mock()
    return m


ADDR = 0x1000


class TestCounter:
    def test_register_handler_default_increment(self, qemu):
        counter = Counter()
        handler = counter.register_handler(qemu, ADDR, "my_counter")
        assert counter.increment[ADDR] == 1
        assert counter.counts[ADDR] == 0
        assert handler is Counter.get_value

    def test_register_handler_custom_increment(self, qemu):
        counter = Counter()
        handler = counter.register_handler(qemu, ADDR, "my_counter", increment=5)
        assert counter.increment[ADDR] == 5
        assert counter.counts[ADDR] == 0

    def test_get_value_increments(self, qemu):
        counter = Counter()
        counter.increment[ADDR] = 1
        counter.counts[ADDR] = 0

        intercept, ret = counter.get_value(qemu, ADDR)
        assert intercept is True
        assert ret == 1
        assert counter.counts[ADDR] == 1

    def test_get_value_increments_by_custom_amount(self, qemu):
        counter = Counter()
        counter.increment[ADDR] = 10
        counter.counts[ADDR] = 0

        intercept, ret = counter.get_value(qemu, ADDR)
        assert intercept is True
        assert ret == 10

        intercept, ret = counter.get_value(qemu, ADDR)
        assert ret == 20

    def test_get_value_multiple_addresses(self, qemu):
        counter = Counter()
        addr2 = 0x2000
        counter.increment[ADDR] = 1
        counter.counts[ADDR] = 0
        counter.increment[addr2] = 2
        counter.counts[addr2] = 0

        counter.get_value(qemu, ADDR)
        counter.get_value(qemu, addr2)

        assert counter.counts[ADDR] == 1
        assert counter.counts[addr2] == 2
