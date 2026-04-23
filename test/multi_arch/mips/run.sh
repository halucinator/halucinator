#!/bin/bash

PYTHONUNBUFFERED=1 halucinator -c test/multi_arch/mips/test_uart_config.yaml \
  -c test/multi_arch/mips/test_uart_addrs.yaml \
  -c test/multi_arch/mips/test_uart_memory.yaml --log_blocks=trace-nochain -n mips_uart_test
