#!/bin/bash

PYTHONUNBUFFERED=1 halucinator --emulator "${HAL_EMULATOR:-avatar2}" \
  -c test/multi_arch/ppc/test_uart_config.yaml \
  -c test/multi_arch/ppc/test_uart_addrs.yaml \
  -c test/multi_arch/ppc/test_uart_memory.yaml \
  --log_blocks=trace-nochain -n ppc_uart_test
