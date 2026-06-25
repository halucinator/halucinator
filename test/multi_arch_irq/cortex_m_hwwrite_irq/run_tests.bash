#!/usr/bin/env bash
# Copyright 2026 Christopher Wright

# Regression test for upstream issue #31 — asserting an IRQ from inside a
# peripheral hw_write handler (forwarded-MMIO context).
#
# The firmware writes to the IrqOnWritePeripheral, whose hw_write handler
# asks HALucinator to assert IRQ 17. The injection method is chosen by the
# HAL_REPRO_INJECT_METHOD env var (default "deferred"):
#
#   deferred -> peripheral_server.inject_irq_deferred (THE FIX): the inject
#               is handed to the IRQ worker thread, hw_write returns, and
#               the IRQ wakes the WFI-halted core. Expect PASS.
#   qmp      -> peripheral_server.inject_irq inline (the original bug):
#               QMP inject from the MMIO thread deadlocks. Expect the
#               firmware to hang after "READY" (demonstrates issue #31).
#
# PASS = "WROTE" and "IRQ 17 FIRED" both appear.

export HAL_REPRO_INJECT_METHOD="${HAL_REPRO_INJECT_METHOD:-deferred}"
set -x

pkill -9 -f qemu-system-arm 2>/dev/null || true
pkill -9 -f halucinator 2>/dev/null || true
pkill -9 -f gdb-multiarch 2>/dev/null || true
sleep 2

rm -f ./hal_out.txt; : > hal_out.txt
rm -rf tmp/cortex_m_hwwrite_irq_test

[ -n "${HALUCINATOR_QEMU_ARM:-}" ] && export HALUCINATOR_QEMU_ARM
PYTHONUNBUFFERED=1 \
    bash ./test/multi_arch_irq/cortex_m_hwwrite_irq/run.sh </dev/null \
    > hal_out.txt 2>&1 &
HAL_PID=$!

# Wait for READY.
ELAPSED=0
while [ $ELAPSED -lt 60 ]; do
    grep -q "UART TX:b'READY" hal_out.txt 2>/dev/null && break
    kill -0 $HAL_PID 2>/dev/null || { echo "halucinator exited before READY"; tail -40 hal_out.txt; exit 1; }
    sleep 1; ELAPSED=$((ELAPSED + 1))
done

# The firmware's MMIO write now drives the synchronous inject. Give it
# time to either complete (fixed) or deadlock (bug).
ELAPSED=0
SUCCESS=0
while [ $ELAPSED -lt 30 ]; do
    if grep -q "IRQ 17 FIRED" hal_out.txt 2>/dev/null; then SUCCESS=1; break; fi
    sleep 1; ELAPSED=$((ELAPSED + 1))
done

pkill -9 -f qemu-system-arm 2>/dev/null || true
pkill -9 -f halucinator 2>/dev/null || true
pkill -9 -f gdb-multiarch 2>/dev/null || true

if [ "$SUCCESS" = "1" ]; then
    echo "PASS: inject from hw_write (method=$HAL_REPRO_INJECT_METHOD) woke WFI"
    exit 0
fi
echo "FAIL/REPRODUCED: inject from hw_write (method=$HAL_REPRO_INJECT_METHOD) did not wake the core"
echo "Markers seen:"; grep -aE "UART TX:b.(READY|WROTE)|IRQ 17 FIRED" hal_out.txt || true
exit 1
