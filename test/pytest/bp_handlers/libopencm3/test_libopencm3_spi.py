# Copyright 2022 GrammaTech Inc.
from unittest import mock

import pytest

from halucinator.bp_handlers.libopencm3.libopencm3_spi import LIBOPENCM3_SPI


@pytest.fixture
def qemu():
    mock_model = mock.Mock()
    return mock_model


@pytest.fixture
def spi():
    mock_model = mock.Mock()
    return LIBOPENCM3_SPI(mock_model)


class TestLIBOPENCM3_SPI:
    def test_hal_init_just_returns_zero(self, spi):
        # Associated HAL fuction declaration
        # int
        # spi_init_master (
        #   uint32_t spi,
        #   uint32_t br,
        #   uint32_t cpol,
        #   uint32_t cpha,
        #   uint32_t dff,
        #   uint32_t lsbfirst
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/4a378a729a9f9b7f24e527e74dd38b5ae3b9bc69/lib/stm32/common/spi_common_l1f124.c#L94
        continue_, retval = spi.hal_init(None, None)
        assert continue_
        assert retval == 0

    def test_hal_ok_just_returns_zero(self, spi):
        # Associated HAL fuctions declaration
        # The under test functions' description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/4a378a729a9f9b7f24e527e74dd38b5ae3b9bc69/lib/stm32/common/spi_common_all.c
        continue_, retval = spi.hal_ok(None, None)
        assert continue_
        assert retval == 0
