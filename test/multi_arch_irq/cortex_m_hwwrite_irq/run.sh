#!/bin/bash
# Copyright 2026 Christopher Wright

PYTHONUNBUFFERED=1 halucinator --emulator "${HAL_EMULATOR:-avatar2}" \
  -c test/multi_arch_irq/cortex_m_hwwrite_irq/test_irq_config.yaml \
  -c test/multi_arch_irq/cortex_m_hwwrite_irq/test_irq_addrs.yaml \
  -c test/multi_arch_irq/cortex_m_hwwrite_irq/test_irq_memory.yaml \
  --log_blocks=trace-nochain -n cortex_m_hwwrite_irq_test
