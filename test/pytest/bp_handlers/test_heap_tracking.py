"""Tests for halucinator.bp_handlers.generic.heap_tracking module."""

from unittest import mock

import pytest

from halucinator.bp_handlers.generic.heap_tracking import Alloc


@pytest.fixture
def qemu():
    m = mock.Mock()
    m.regs = mock.Mock()
    return m


ADDR = 0x1000


# ---------------------------------------------------------------------------
# Alloc -- malloc
# ---------------------------------------------------------------------------


class TestMalloc:
    def test_malloc_increases_size_by_8(self, qemu):
        alloc = Alloc(use_cookie=False)
        qemu.get_arg.return_value = 100  # requested size
        qemu.regs.lr = 0x2000

        intercept, ret = alloc.malloc(qemu, ADDR)

        assert qemu.regs.r0 == 108  # 100 + 8
        assert alloc.item_size[0x2000] == 4
        assert alloc.memory_size[0x2000] == 100
        qemu.set_bp.assert_called_once_with(
            0x2000, alloc, "alloc_return_handler", run_once=True
        )
        assert intercept is False
        assert ret == 0


# ---------------------------------------------------------------------------
# Alloc -- calloc
# ---------------------------------------------------------------------------


class TestCalloc:
    def test_calloc_increases_nitems_by_2(self, qemu):
        alloc = Alloc(use_cookie=False)
        qemu.get_arg.side_effect = [10, 4]  # nitems=10, size=4
        qemu.regs.lr = 0x3000

        intercept, ret = alloc.calloc(qemu, ADDR)

        assert qemu.regs.r0 == 12  # 10 + 2
        assert alloc.item_size[0x3000] == 4
        assert alloc.memory_size[0x3000] == 40  # 10 * 4
        qemu.set_bp.assert_called_once_with(
            0x3000, alloc, "alloc_return_handler", run_once=True
        )
        assert intercept is False
        assert ret == 0


# ---------------------------------------------------------------------------
# Alloc -- free
# ---------------------------------------------------------------------------


class TestFree:
    def test_free_unallocated_memory_reports_canary(self, qemu):
        alloc = Alloc(use_cookie=False)
        alloc.model = mock.Mock()
        qemu.get_arg.return_value = 0x5000  # never allocated

        intercept, ret = alloc.free(qemu, ADDR)

        alloc.model.canary.assert_called_once()
        assert intercept is True
        assert ret is None

    def test_free_double_free_reports_canary(self, qemu):
        alloc = Alloc(use_cookie=False)
        alloc.model = mock.Mock()
        alloc.is_valid[0x5000] = False  # already freed
        qemu.get_arg.return_value = 0x5000

        intercept, ret = alloc.free(qemu, ADDR)

        alloc.model.canary.assert_called_once()
        assert "already been freed" in alloc.model.canary.call_args[0][3]
        assert intercept is True
        assert ret is None

    def test_free_removes_watchpoints(self, qemu):
        alloc = Alloc(use_cookie=False)
        alloc.model = mock.Mock()
        src = 0x5000
        alloc.is_valid[src] = True
        alloc.item_size[src] = 4
        alloc.memory_size[src] = 100
        alloc.watchpoint[src] = (10, 11)
        qemu.get_arg.return_value = src

        intercept, ret = alloc.free(qemu, ADDR)

        qemu.remove_breakpoint.assert_any_call(10)
        qemu.remove_breakpoint.assert_any_call(11)
        assert alloc.is_valid[src] is False
        assert intercept is False
        assert ret is None

    def test_free_with_cookie_checks_values(self, qemu):
        alloc = Alloc(use_cookie=True)
        alloc.model = mock.Mock()
        src = 0x5000
        cookie = 0xDEAD
        alloc.is_valid[src] = True
        alloc.item_size[src] = 4
        alloc.memory_size[src] = 100
        alloc.cookie[src] = cookie
        qemu.get_arg.return_value = src
        # Pre-cookie and post-cookie both match
        qemu.read_memory.side_effect = [cookie, cookie]

        intercept, ret = alloc.free(qemu, ADDR)

        alloc.model.canary.assert_not_called()
        assert intercept is False

    def test_free_with_cookie_detects_underflow(self, qemu):
        alloc = Alloc(use_cookie=True)
        alloc.model = mock.Mock()
        src = 0x5000
        cookie = 0xDEAD
        alloc.is_valid[src] = True
        alloc.item_size[src] = 4
        alloc.memory_size[src] = 100
        alloc.cookie[src] = cookie
        qemu.get_arg.return_value = src
        # Pre-cookie changed, post-cookie ok
        qemu.read_memory.side_effect = [0xBEEF, cookie]

        alloc.free(qemu, ADDR)

        # Should report underflow
        calls = alloc.model.canary.call_args_list
        assert any("underflow" in str(c).lower() for c in calls)

    def test_free_with_cookie_detects_overflow(self, qemu):
        alloc = Alloc(use_cookie=True)
        alloc.model = mock.Mock()
        src = 0x5000
        cookie = 0xDEAD
        alloc.is_valid[src] = True
        alloc.item_size[src] = 4
        alloc.memory_size[src] = 100
        alloc.cookie[src] = cookie
        qemu.get_arg.return_value = src
        # Pre-cookie ok, post-cookie changed
        qemu.read_memory.side_effect = [cookie, 0xBEEF]

        alloc.free(qemu, ADDR)

        calls = alloc.model.canary.call_args_list
        assert any("overflow" in str(c).lower() for c in calls)


# ---------------------------------------------------------------------------
# Alloc -- alloc_return_handler (watchpoint mode)
# ---------------------------------------------------------------------------


class TestAllocReturnHandler:
    def test_watchpoint_mode(self, qemu):
        alloc = Alloc(use_cookie=False)
        link_reg = 0x2000
        alloc.memory_size[link_reg] = 100
        alloc.item_size[link_reg] = 4
        qemu.regs.r0 = 0x8000  # base of expanded region
        qemu.set_bp.side_effect = [42, 43]

        intercept, ret = alloc.alloc_return_handler(qemu, link_reg)

        # Should mark as valid at src + item_size
        assert alloc.is_valid[0x8004] is True
        assert alloc.watchpoint[0x8004] == (42, 43)
        assert intercept is False

    def test_cookie_mode(self, qemu):
        alloc = Alloc(use_cookie=True)
        link_reg = 0x2000
        alloc.memory_size[link_reg] = 100
        alloc.item_size[link_reg] = 4
        qemu.regs.r0 = 0x8000

        intercept, ret = alloc.alloc_return_handler(qemu, link_reg)

        assert alloc.is_valid[0x8004] is True
        assert 0x8004 in alloc.cookie
        # write_memory should be called twice (pre and post cookie)
        assert qemu.write_memory.call_count == 2


# ---------------------------------------------------------------------------
# Alloc -- realloc
# ---------------------------------------------------------------------------


class TestRealloc:
    def test_realloc_watchpoint_mode(self, qemu):
        alloc = Alloc(use_cookie=False)
        src = 0x5000
        alloc.item_size[src] = 4
        alloc.memory_size[src] = 100
        alloc.watchpoint[src] = (10, 11)
        qemu.get_arg.side_effect = [src, 200]  # ptr, new_size
        qemu.regs.lr = 0x3000

        intercept, ret = alloc.realloc(qemu, ADDR)

        qemu.remove_breakpoint.assert_any_call(10)
        qemu.remove_breakpoint.assert_any_call(11)
        assert qemu.regs.r0 == src - 4  # adjusted back
        assert qemu.regs.r1 == 200 + 8  # new_size + 2 * item_size
        assert alloc.memory_size[0x3000] == 200
        assert intercept is False

    def test_realloc_cookie_mode(self, qemu):
        alloc = Alloc(use_cookie=True)
        src = 0x5000
        alloc.item_size[src] = 4
        alloc.memory_size[src] = 100
        alloc.cookie[src] = 0xCAFE
        qemu.get_arg.side_effect = [src, 200]
        qemu.regs.lr = 0x3000

        intercept, ret = alloc.realloc(qemu, ADDR)

        assert alloc.cookie[0x3000] == 0xCAFE
        assert intercept is False


# ---------------------------------------------------------------------------
# Alloc -- realloc_return_handler
# ---------------------------------------------------------------------------


class TestReallocReturnHandler:
    def test_watchpoint_mode(self, qemu):
        alloc = Alloc(use_cookie=False)
        addr = 0x3000
        alloc.item_size[addr] = 4
        alloc.memory_size[addr] = 200
        qemu.regs.r0 = 0x9000
        qemu.set_bp.side_effect = [50, 51]

        intercept, ret = alloc.realloc_return_handler(qemu, addr)

        assert alloc.is_valid[0x9004] is True
        assert alloc.watchpoint[0x9004] == (50, 51)
        assert intercept is False

    def test_cookie_mode(self, qemu):
        alloc = Alloc(use_cookie=True)
        addr = 0x3000
        alloc.item_size[addr] = 4
        alloc.memory_size[addr] = 200
        alloc.cookie[addr] = 0xBEEF
        qemu.regs.r0 = 0x9000

        intercept, ret = alloc.realloc_return_handler(qemu, addr)

        assert alloc.cookie[0x9004] == 0xBEEF
        # Only end cookie is written for realloc
        qemu.write_memory.assert_called_once()


# ---------------------------------------------------------------------------
# Alloc -- handle_overflow
# ---------------------------------------------------------------------------


class TestHandleOverflow:
    def test_handle_overflow(self, qemu):
        alloc = Alloc()
        alloc.model = mock.Mock()

        intercept, ret = alloc.handle_overflow(qemu, ADDR)

        alloc.model.canary.assert_called_once()
        assert intercept is False
        assert ret == 0
