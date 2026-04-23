# Copyright 2022 GrammaTech Inc.
from unittest import mock

import pytest

from halucinator.bp_handlers.libopencm3.libopencm3_dma import LIBOPENCM3_DMA


@pytest.fixture
def qemu():
    mock_model = mock.Mock()
    return mock_model


@pytest.fixture
def dma():
    mock_model = mock.Mock()
    return LIBOPENCM3_DMA(mock_model)


class TestLIBOPENCM3_SPI:
    def test_hal_ok_just_returns_zero(self, dma):
        # Associated HAL fuctions declaration
        # The under test functions' description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/4a378a729a9f9b7f24e527e74dd38b5ae3b9bc69/lib/stm32/common/spi_common_all.c
        continue_, retval = dma.hal_ok(None, None)
        assert continue_
        assert retval == 0
