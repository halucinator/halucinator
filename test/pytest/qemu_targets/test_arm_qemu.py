import os
from dataclasses import dataclass
from pathlib import Path

import pytest
from avatar2 import Avatar, archs
from halucinator import hal_config

from halucinator.qemu_targets.arm_qemu import ARMQemuTarget
from halucinator.qemu_targets.armv7m_qemu import ARMv7mQemuTarget


def round_up(size):
    return (size & ~0xFFF) + 0x1000


FIRMWARE = Path(__file__).parent / "test-exes" / "test-arm.bin"
SIZE = round_up(os.path.getsize(FIRMWARE))

# Several addresses from the executable.
@dataclass
class Addresses:
    load: int
    main: int
    arguments_check: int
    return_site_from_arguments_check: int
    breakpoint_check_arguments_check_return: int
    return_12: int
    breakpoint_check_twelve: int


# Generate with test-exes/find-addresses.py
ADDRS = Addresses(
    load=0x8000,
    main=0x830C,
    arguments_check=0x824C,
    breakpoint_check_arguments_check_return=0x82B8,
    return_12=0x82D0,
    breakpoint_check_twelve=0x82EC,
    return_site_from_arguments_check=0x833C,
)

# The stack address is arbitrary, and stack size conservative
STACK_ADDRESS = 0x100000
STACK_SIZE = 0x8000
HALR_ADDRESS = 0x200000
HALR_SIZE = 0x8000


def set_up_avatar_qemu(impl_class):
    avatar = Avatar(arch=archs.ARM)
    memconf = hal_config.HalMemConfig(
        "halucinator", "/tmp/cfg.txt", 4096, 8192, "r", "file.txt", True
    )
    config = hal_config.HalucinatorConfig()
    config.memories["halucinator"] = memconf
    avatar.config = config

    qemu = avatar.add_target(
        impl_class,
        name="qemu1",
        cpu_model="cortex-m3",
        executable=os.getenv("HALUCINATOR_QEMU_ARM"),
    )

    avatar.add_memory_range(
        ADDRS.load, SIZE, "text", file=FIRMWARE,
    )
    # I don't want to worry about stack growing up/down, if sp points
    # to the current top or the next slot, or what, so let's just
    # point sp to the middle of the region.
    avatar.add_memory_range(
        STACK_ADDRESS - STACK_SIZE, 2 * STACK_SIZE, "stack",
    )

    avatar.add_memory_range(
        HALR_ADDRESS, HALR_SIZE, "halucinator",
    )

    avatar.config.memories = avatar.memory_ranges

    avatar.init_targets()

    qemu.regs.pc = ADDRS.main
    qemu.regs.sp = STACK_ADDRESS

    try:
        yield qemu
    finally:
        avatar.shutdown()


@pytest.fixture
def avatar_qemu():
    for avatar_qemu in set_up_avatar_qemu(ARMQemuTarget):
        yield avatar_qemu


@pytest.fixture
def avatar_qemu_v7m():
    for avatar_qemu in set_up_avatar_qemu(ARMv7mQemuTarget):
        yield avatar_qemu


# @pytest.mark.avatar
# def test_get_arg_returns_register_arguments_as_expected(avatar_qemu):
#     avatar_qemu.set_breakpoint(ADDRS.arguments_check)
#     avatar_qemu.cont()
#     avatar_qemu.wait()

#     assert avatar_qemu.get_arg(0) == 1
#     assert avatar_qemu.get_arg(1) == 2
#     assert avatar_qemu.get_arg(2) == 3
#     assert avatar_qemu.get_arg(3) == 4
#     # Arguments 5 and 6 are on the stack and tested by the next test.


# @pytest.mark.avatar
# def test_get_arg_returns_stack_arguments_as_expected(avatar_qemu):
#     avatar_qemu.set_breakpoint(ADDRS.arguments_check)
#     avatar_qemu.cont()
#     avatar_qemu.wait()

#     # Arguments 0-3 are in registers and tested by the previous test.
#     assert avatar_qemu.get_arg(4) == 5
#     assert avatar_qemu.get_arg(5) == 6


# @pytest.mark.avatar
# def test_arguments_check_return_value_reaches_checker_in_r0(avatar_qemu):
#     # This test is kind of a sanity check. The next one below will
#     # make sure we can change the arguments passed to 'argument_check'
#     # and have those changed arguments take effect, but that will also
#     # check the return value from that function. This function makes
#     # sure that we're looking at the right place to start with.
#     avatar_qemu.set_breakpoint(ADDRS.breakpoint_check_arguments_check_return)
#     avatar_qemu.cont()
#     avatar_qemu.wait()

#     assert avatar_qemu.regs.r0 == 0x654321


# @pytest.mark.avatar
# def test_arguments_check_can_reassign_arguments_and_they_take_effect(
#     avatar_qemu,
# ):
#     avatar_qemu.set_breakpoint(ADDRS.arguments_check)
#     avatar_qemu.cont()
#     avatar_qemu.wait()

#     # We are right at the entrance to arguments_check. Let's change
#     # the arguments to make arguments_check produce a different
#     # value...
#     avatar_qemu.set_arg(0, 2)
#     avatar_qemu.set_arg(1, 3)
#     avatar_qemu.set_arg(2, 4)
#     avatar_qemu.set_arg(3, 5)
#     avatar_qemu.set_arg(4, 6)
#     avatar_qemu.set_arg(5, 7)

#     # ...then run it...
#     avatar_qemu.set_breakpoint(ADDRS.breakpoint_check_arguments_check_return)
#     avatar_qemu.cont()
#     avatar_qemu.wait()

#     # ...and make sure the return value got changed to what we want.
#     assert avatar_qemu.regs.r0 == 0x765432


# @pytest.mark.avatar
# def test_get_ret_addr_at_arguments_check_refers_to_return_site(avatar_qemu):
#     avatar_qemu.set_breakpoint(ADDRS.arguments_check)
#     avatar_qemu.cont()
#     avatar_qemu.wait()

#     assert avatar_qemu.get_ret_addr() == ADDRS.return_site_from_arguments_check


# @pytest.mark.avatar
# @pytest.mark.parametrize("should_set_ret_addr", [True, False])
# def test_set_ret_addr_can_hijack_return(avatar_qemu, should_set_ret_addr):
#     # 'should_set_ret_addr' is a test for the test -- I want to make
#     # sure there isn't interference between the breakpoints or anything.
#     avatar_qemu.set_breakpoint(ADDRS.arguments_check)
#     avatar_qemu.cont()
#     avatar_qemu.wait()

#     if should_set_ret_addr:
#         avatar_qemu.set_ret_addr(ADDRS.breakpoint_check_twelve)
#     avatar_qemu.set_breakpoint(ADDRS.breakpoint_check_twelve)
#     avatar_qemu.set_breakpoint(ADDRS.return_12)
#     avatar_qemu.cont()
#     avatar_qemu.wait()

#     # The set_ret_addr(), if executed, should mean that the call to
#     # return_12() (and more) is skipped, so we should now be at
#     # return_12()
#     if should_set_ret_addr:
#         assert avatar_qemu.regs.pc == ADDRS.breakpoint_check_twelve
#     else:
#         assert avatar_qemu.regs.pc == ADDRS.return_12
#     assert avatar_qemu.regs.r0 == 0x654321


# @pytest.mark.avatar
# def test_execute_return_preempts_current_function(avatar_qemu):
#     avatar_qemu.set_breakpoint(ADDRS.return_12)
#     avatar_qemu.cont()
#     avatar_qemu.wait()

#     avatar_qemu.execute_return(17)

#     avatar_qemu.set_breakpoint(ADDRS.breakpoint_check_twelve)
#     avatar_qemu.cont()
#     avatar_qemu.wait()

#     assert avatar_qemu.regs.r0 == 17  # not 12


# @pytest.mark.avatar
# def test_execute_return_with_None_still_preempts_current_function_just_doesnt_set_return_value(
#     avatar_qemu,
# ):
#     avatar_qemu.set_breakpoint(ADDRS.return_12)
#     avatar_qemu.cont()
#     avatar_qemu.wait()

#     avatar_qemu.execute_return(None)

#     avatar_qemu.set_breakpoint(ADDRS.breakpoint_check_twelve)
#     avatar_qemu.cont()
#     avatar_qemu.wait()

#     assert avatar_qemu.regs.r0 == 0x654321  # leftover


####################
# Interrupt handling
#
# This seems to me like it's all just broken/incomplete. The following
# tests I have as "positive" tests that pass rather than xfail tests
# that expectedly fail because I'm not actually sure how they should
# be used...
@pytest.mark.avatar
def test_irq_set_qmp_raises_without_irq_controller(avatar_qemu):
    """irq_set_qmp raises TypeError when no halucinator-irq memory is configured"""
    with pytest.raises(TypeError, match="No Interrupt Controller found"):
        avatar_qemu.irq_set_qmp()


@pytest.mark.avatar
def test_irq_clear_qmp_raises_without_irq_controller(avatar_qemu):
    """irq_clear_qmp raises TypeError when no halucinator-irq memory is configured"""
    with pytest.raises(TypeError, match="No Interrupt Controller found"):
        avatar_qemu.irq_clear_qmp()


@pytest.mark.avatar
def test_irq_enable_qmp_raises_without_irq_controller(avatar_qemu):
    """irq_enable_qmp raises TypeError when no halucinator-irq memory is configured"""
    with pytest.raises(TypeError, match="No Interrupt Controller found"):
        avatar_qemu.irq_enable_qmp()


@pytest.mark.avatar
@pytest.mark.xfail(reason="avatar-armv7m QMP commands not available in test QEMU")
def test_enable_interrupt_is_missing_a_required_parameter_to_qemu(
    avatar_qemu_v7m,
):
    result = avatar_qemu_v7m.enable_interrupt(1)
    assert result["desc"] == "Parameter 'irq-rx-queue-name' is missing"


@pytest.mark.avatar
@pytest.mark.xfail(reason="avatar-armv7m QMP commands not available in test QEMU")
def test_trigger_interrupt_breaks_the_connection(avatar_qemu_v7m):
    with pytest.raises(ConnectionResetError):
        avatar_qemu_v7m.trigger_interrupt(0)


# No test for set_vector_table_base because I want to check how that
# is *used*, but the other interrupt handling is broken, so I can't
# really do that. I'm also having a very difficult time figuring out
# how to even work the interrupt handling at all.
