"""
Unit test for hal logging
"""

import logging
import logging.config

from halucinator import hal_log


class TestHalLog:
    def test_hal_logger_set_correctly(self):
        assert hal_log.getHalLogger() == logging.getLogger(hal_log.HAL_LOGGER)
