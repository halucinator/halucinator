#!/bin/bash
PYTHONUNBUFFERED=1 halucinator --emulator "${HAL_EMULATOR:-unicorn}" \
  -c test/multi_arch_irq/arm64/test_irq_config.yaml \
  -c test/multi_arch_irq/arm64/test_irq_addrs.yaml \
  -c test/multi_arch_irq/arm64/test_irq_memory.yaml \
  --log_blocks=trace-nochain -n arm64_irq_test
