import time

import pytest

from halucinator.bp_handlers.atmel_asf_v3.contiki import Contiki


@pytest.fixture
def contiki():
    ctk = Contiki()
    return ctk


class TestContiki:
    # Unfortunately we cannot mock time.time().
    # The contiki.clock_time function will return 0 values instead of 2.
    # It is a known Python issue. Likely related to this one - https://bugs.python.org/issue34716
    @pytest.mark.parametrize("delay,ticks", [(1, 128), (0.5, 64), (2, 256)])
    def test_clock_time_returns_number_of_ticks_correctly(
        self, contiki, delay, ticks
    ):
        # Associated HAL fuction declaration
        # CCIF clock_time_t
        # clock_time (
        #   void
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/samd21/html/group__clock.html#ga50c22f9b9d60dd1f9e59b63a3a6676b1
        time.sleep(delay)
        continue_, retval = contiki.clock_time(None, None)
        assert continue_
        assert retval == ticks

    @pytest.mark.parametrize("delay,seconds", [(1, 1), (2, 2), (4, 4)])
    def test_seconds_returns_number_of_seconds_correctly(
        self, contiki, delay, seconds
    ):
        # Associated HAL fuction declaration
        # CCIF unsigned long
        # clock_seconds (
        #   void
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/samd21/html/group__clock.html#ga8bbd5d5a773349139eee79d365af36ab
        time.sleep(delay)
        continue_, retval = contiki.clock_seconds(None, None)
        assert continue_
        assert retval == seconds
