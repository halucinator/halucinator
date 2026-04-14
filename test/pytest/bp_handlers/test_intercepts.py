# NOTE: This is also testing stuff in bp_handlers.py, because of the
# @bp_handler/BPHandler use in bp_handlers_helper.py. But the two
# modules are tightly-coupled enough that I think it's not productive
# to test independently.
#
# We do not test intercepts.{tx,rx}_map because those functions appear
# to be unused.

from unittest import mock

import pytest
from avatar2.message import BreakpointHitMessage
from bp_handler_helpers import (
    DummyBPHandler,
    scope_global_state_of_intercepts_module,
)

from halucinator.bp_handlers import intercepts
from halucinator.hal_config import HalInterceptConfig
from halucinator.qemu_targets.hal_qemu import HALQemuTarget


@pytest.fixture(autouse=True)
def with_registered_bp_handler(tmp_path):
    intercepts.initalized_classes = {}
    intercepts.bp2handler_lut = {}
    for x in scope_global_state_of_intercepts_module(tmp_path):
        yield x


# These are chosen arbitrarily
BP_NUMBER = 17
BP_ADDRESS = 0x1234
WP_NUMBER = 18  # "watchpoint"


@pytest.fixture
def mock_qemu():
    qemu = mock.Mock()
    qemu.set_breakpoint.return_value = BP_NUMBER
    qemu.set_watchpoint.return_value = WP_NUMBER
    return qemu


def _get_kwargs_default(key, default, kwargs):
    """
    Returns the value provided for argument 'key' within 'kwargs' if
    it's there, or 'default' if it's not. If 'key' *was* passed, also
    remove it from kwargs.
    """
    if key in kwargs:
        val = kwargs[key]
        del kwargs[key]
        return val
    else:
        return default


def make_config(**kwargs):
    """
    Creates and returns a HalInterceptConfig with default settings,
    except with those defaults overriden by kwargs.

    Passing 'cls' or 'addr' arguments will override the defaults.

    Passing any other keys will be forwarded to the intercept config.
    """
    handler_class_name = _get_kwargs_default(
        "cls", "bp_handler_helpers.DummyBPHandler", kwargs
    )
    addr = _get_kwargs_default("addr", BP_ADDRESS, kwargs)

    return HalInterceptConfig(
        "dummy.yaml", handler_class_name, "DummyFunction", addr=addr, **kwargs,
    )


def get_handler_instance(breakpoint_number=BP_NUMBER):
    """
    'intercepts' stores a mapping from breakpoint number (as returned
    by the avatar target's 'set_breakpoint' (/'set_watchpoint')
    function during registration) to the BPHandler subclass instance
    that will handle the given intercept.

    This utility function returns that instance.

    (It could return 'handler' as well, the actual function to call,
    but I think I don't want to bother doing anything with that
    directly. The tests of the 'interceptor' function I think
    adequately ensure that is set correctly.)
    """
    bp_info = intercepts.bp2handler_lut[breakpoint_number]
    return bp_info.bp_class


@pytest.fixture
def with_dummy_handler_registered(mock_qemu):
    """
    Registers 'DummyBPHandler' using default initialization.
    """
    intercept_config = make_config()
    intercepts.register_bp_handler(mock_qemu, intercept_config)
    return None


###########################
## Time for actual tests ##
###########################


def test_intercepts_global_state_is_empty_until_register_bp_handler_is_called():
    # This test is technically redundant, but the documentation value
    # I think makes it useful.
    assert intercepts.initalized_classes == {}
    assert intercepts.bp2handler_lut == {}


@mock.patch("halucinator.bp_handlers.intercepts.importlib.import_module")
@mock.patch("halucinator.bp_handlers.intercepts.getattr")
def test_get_bp_handler_debug(attr_mock, import_mock):
    cls = "test.test.Test"

    class_mock = mock.Mock()

    import_mock.return_value = 1
    attr_mock.return_value = class_mock

    intercepts.get_bp_handler_debug(cls, test="test")

    import_mock.assert_called_once_with("test.test")
    attr_mock.assert_called_once_with(1, "Test")
    class_mock.assert_called_once_with(test="test")


def test_register_bp_handler_instantiates_requested_class_and_records_handler(
    with_dummy_handler_registered, mock_qemu
):
    handler_instance = get_handler_instance()

    # The handler class is of the expected type...
    assert isinstance(handler_instance, DummyBPHandler)

    # ...it was instantiated as expected (just 'DummyBPHandler()')...
    assert handler_instance.init_args == {}

    # ...the handler hasn't been called yet for some reason...
    assert not handler_instance.handler_called

    # ...and there was also a breakpoint set at the QEMU level.
    mock_qemu.set_breakpoint.assert_called_with(BP_ADDRESS, temporary=False)

    # This is just a sanity check for the counterpart of the next test.
    mock_qemu.set_watchpoint.assert_not_called()


def test_register_bp_handler_passes_temporary_True_with_run_once(mock_qemu):
    intercept_config = make_config(run_once=True)

    intercepts.register_bp_handler(mock_qemu, intercept_config)

    assert isinstance(get_handler_instance(), DummyBPHandler)
    mock_qemu.set_breakpoint.assert_called_with(BP_ADDRESS, temporary=True)


def test_register_bp_handler_uses_set_watchpoint_when_read_watchpoint_is_requested(
    mock_qemu,
):
    intercept_config = make_config(watchpoint="r")

    intercepts.register_bp_handler(mock_qemu, intercept_config)

    assert isinstance(get_handler_instance(WP_NUMBER), DummyBPHandler)
    mock_qemu.set_breakpoint.assert_not_called()
    mock_qemu.set_watchpoint.assert_called_with(
        BP_ADDRESS, read=True, write=False
    )


def test_register_bp_handler_uses_set_watchpoint_when_write_watchpoint_is_requested(
    mock_qemu,
):
    intercept_config = make_config(watchpoint="w")

    intercepts.register_bp_handler(mock_qemu, intercept_config)

    assert isinstance(get_handler_instance(WP_NUMBER), DummyBPHandler)
    mock_qemu.set_breakpoint.assert_not_called()
    mock_qemu.set_watchpoint.assert_called_with(
        BP_ADDRESS, read=False, write=True
    )


def test_register_bp_handler_uses_set_watchpoint_when_readwrite_watchpoint_is_requested(
    mock_qemu,
):
    intercept_config = make_config(watchpoint="rw")

    intercepts.register_bp_handler(mock_qemu, intercept_config)

    assert isinstance(get_handler_instance(WP_NUMBER), DummyBPHandler)
    mock_qemu.set_breakpoint.assert_not_called()
    mock_qemu.set_watchpoint.assert_called_with(
        BP_ADDRESS, read=True, write=True
    )


def test_register_bp_handler_instantiates_requested_class_with_specified_class_args(
    mock_qemu,
):
    HANDLER_CLASS_ARGS = {"first": 0x17, "second": 0x42}
    intercept_config = make_config(class_args=HANDLER_CLASS_ARGS)

    intercepts.register_bp_handler(mock_qemu, intercept_config)

    handler_instance = get_handler_instance()
    assert handler_instance.init_args == HANDLER_CLASS_ARGS


def test_register_bp_handler_registers_handler_with_specified_registration_args(
    mock_qemu,
):
    REGISTER_EXTRA_ARGS = {"first": 0x17, "second": 0x42}
    intercept_config = make_config(
        cls="bp_handler_helpers.DummyBPHandlerWithRegisterHandlerFunction",
        registration_args=REGISTER_EXTRA_ARGS,
    )

    intercepts.register_bp_handler(mock_qemu, intercept_config)

    handler_instance = get_handler_instance()
    assert handler_instance.init_args == {}
    assert handler_instance.registration_args == REGISTER_EXTRA_ARGS


def test_register_bp_handler_only_constructs_a_given_class_once(mock_qemu):
    FIRST_BP_NUMBER = 17
    SECOND_BP_NUMBER = 18

    # First potential construction (construction occurs)
    mock_qemu.set_breakpoint.return_value = FIRST_BP_NUMBER
    intercept_config = make_config(
        addr=0x1234, class_args={"instantiation": 1},
    )
    intercepts.register_bp_handler(mock_qemu, intercept_config)

    # Second potential construction (construction does not occur,
    # register_bp_handlers just reuses the object constructed above,
    # even though the 'class_args' differ)
    mock_qemu.set_breakpoint.return_value = SECOND_BP_NUMBER
    intercept_config = make_config(
        addr=0x2345, class_args={"instantiation": 2},
    )
    intercepts.register_bp_handler(mock_qemu, intercept_config)

    assert len(intercepts.initalized_classes) == 1

    first_handler = get_handler_instance(FIRST_BP_NUMBER)
    second_handler = get_handler_instance(SECOND_BP_NUMBER)

    # But the real point of this test: both of these are '1',
    # instead of 'second_handler.init_args[...] == 2'.
    assert first_handler.init_args["instantiation"] == 1
    assert second_handler.init_args["instantiation"] == 1


def test_interceptor_calls_the_handler(with_dummy_handler_registered):
    handler_instance = get_handler_instance()
    intercepts.pass_breakpoint = True
    intercepts.debug_session = False

    avatar_target = mock.Mock(name="avatar_target")
    avatar_target.regs.pc = BP_ADDRESS
    # Setting .__class__ is so that the mock passes an 'isinstance'
    # assertion in interceptor(). This is a semi-endorsed way to
    # handle this situation. (It might be better to use a mock spec,
    # but also more complex.)
    avatar_target.__class__ = HALQemuTarget
    message = BreakpointHitMessage(
        origin=avatar_target, breakpoint_number=BP_NUMBER, address=BP_ADDRESS,
    )

    assert not handler_instance.handler_called  # sanity and clarity
    intercepts.interceptor(None, message)

    assert handler_instance.handler_called
    assert handler_instance.qemu is avatar_target
    assert handler_instance.bp_addr == BP_ADDRESS
    avatar_target.execute_return.assert_called_with(
        handler_instance.HANDLER_RETURN
    )
    avatar_target.cont.assert_called_with()


def test_interceptor_passes_the_address_from_pc_to_the_handler(
    with_dummy_handler_registered,
):
    # By "the address from PC", that means *not* the address from the
    # 'address' field in the message, and not the address used when
    # registering the handler. We'll use BP_ADDRESS for the latter
    # two, and something different for the first.
    PC_VAL = BP_ADDRESS + 100

    handler_instance = get_handler_instance()

    avatar_target = mock.Mock(name="avatar_target")
    avatar_target.regs.pc = PC_VAL
    avatar_target.__class__ = HALQemuTarget  # see same assignment above
    message = BreakpointHitMessage(
        origin=avatar_target, breakpoint_number=BP_NUMBER, address=BP_ADDRESS,
    )

    intercepts.interceptor(None, message)

    assert handler_instance.bp_addr == PC_VAL
