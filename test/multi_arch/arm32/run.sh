#!/bin/bash

PYTHONUNBUFFERED=1 halucinator -c test/multi_arch/arm32/test_uart_config.yaml \
  -c test/multi_arch/arm32/test_uart_addrs.yaml \
  -c test/multi_arch/arm32/test_uart_memory.yaml --log_blocks=trace-nochain -n arm32_uart_test
