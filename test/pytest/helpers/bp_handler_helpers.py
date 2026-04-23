from halucinator import hal_stats
from halucinator.bp_handlers import intercepts
from halucinator.bp_handlers.bp_handler import BPHandler, bp_handler


class DummyBPHandler(BPHandler):
    HANDLER_RETURN = 11235

    def __init__(self, **kwargs):
        self.init_args = kwargs
        self.handler_called = False

    @bp_handler(["DummyFunction"])
    def handler(self, qemu, bp_addr):
        self.handler_called = True
        self.qemu = qemu
        self.bp_addr = bp_addr
        return True, self.HANDLER_RETURN


class DummyBPHandlerWithRegisterHandlerFunction(BPHandler):
    def __init__(self, **kwargs):
        self.init_args = kwargs
        self.registration_args = {}
        self.handler_called = False

    @bp_handler(["DummyFunction"])
    def handler(self, qemu, bp_addr):
        self.handler_called = True
        self.qemu = qemu
        self.bp_addr = bp_addr
        return True, 0

    def register_handler(self, qemu, bp_addr, function, **kwargs):
        self.registration_args = kwargs
        return super().register_handler(qemu, bp_addr, function)


def scope_global_state_of_intercepts_module(tmp_path):
    """
    Deals with intercept's global state

    There are three things this does.

    First, ensures that 'initalized_classes' [sic] and
    'bp2handler_lut' are clear before beginning the test, and cleans
    up after the test at the end. 'initalized_classes' is a cache that
    means that multiple requests for the same BPHandler subclass only
    result in one instantiation of that class being
    created. 'bp2handler_lut' is how intercepts determines which
    handler to actually run, when a breakpoint is hit.

    'register_bp_handler' adds the requested intercepts to at least
    the latter of those, and can add it to the former as well. To
    prevent interference between tests, we clear these out at the end
    of tests, and as a belt-and-suspenders check, assert that they are
    empty at the start of tests.

    Second, we set a couple required properties on
    'hal_stats.stats'. The 'test_hal_stats' module clears that object
    during the tests for hal_stats runs, so a couple keys that are set
    on that object during module load time (when loading 'intercepts')
    will be cleared, but it assumes they are present. We just restore
    them.

    I thought about clearing at the end, like the others, but I don't
    have any assertions related to these at the moment, so I'll just
    leave it.

    Third, hal_stats needs a filename set, so we set and then clear
    the filename to something in pytest's temporary directory.

    Finally, note the 'autouse=True', which means that each of the
    tests in this file get this "protection". Additionally, Pytest
    runs autouse fixtures (within a scope) before any non-autouse
    fixtures, so this *will* automatically run before anything that
    needs it, including the 'with_dummy_handler_registered' fixture
    below.
    """
    assert intercepts.initalized_classes == {}
    assert intercepts.bp2handler_lut == {}
    hal_stats.stats = {}
    hal_stats.stats["used_intercepts"] = set()
    hal_stats.stats["bypassed_funcs"] = set()
    hal_stats.set_filename(str(tmp_path / "stats.yml"))
    try:
        yield None
    finally:
        intercepts.initalized_classes = {}
        intercepts.bp2handler_lut = {}
        hal_stats._stats_file = None
