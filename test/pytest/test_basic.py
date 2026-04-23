"""
Basic test file used to check that pytest correctly configured for CI
"""
from halucinator import hal_stats


class TestOne:
    def test_basic(self):
        # Just call some dummy function but in the real Halucinator
        # module so we can make sure it imports
        TEST_NAME = "dummy"
        hal_stats.set_filename(TEST_NAME)
        assert hal_stats._stats_file == TEST_NAME
