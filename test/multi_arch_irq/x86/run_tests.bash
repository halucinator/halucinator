#!/usr/bin/env bash
# Copyright 2026 Christopher Wright

# End-to-end IRQ delivery test for the i386 (32-bit x86) firmware.
#
# Boots halucinator with the test_irq firmware, waits for READY in the
# UART output, then injects IRQ 7 via hal_dev_irq_trigger. X86Pic
# synthesises the PC interrupt entry (EIP/CS/EFLAGS frame) and vectors
# the firmware's IRQ_Handler, which sets a flag; main() then prints
# "IRQ 7 FIRED". Test passes iff both READY and "IRQ 7 FIRED" appear.
set -e
set -x

pkill -9 -f x86_irq_test 2>/dev/null || true
pkill -9 -f halucinator 2>/dev/null || true
pkill -9 -f trigger_interrupt 2>/dev/null || true
sleep 2

rm -f ./hal_out.txt
rm -rf tmp/x86_irq_test

PYTHONUNBUFFERED=1 bash ./test/multi_arch_irq/x86/run.sh </dev/null \
    > hal_out.txt 2>&1 &
HAL_PID=$!

TIMEOUT=120
ELAPSED=0
while [ $ELAPSED -lt $TIMEOUT ]; do
    if grep -q "UART TX:b'READY" hal_out.txt 2>/dev/null; then
        echo "Firmware reached READY after ${ELAPSED}s"
        break
    fi
    if ! kill -0 $HAL_PID 2>/dev/null; then
        echo "halucinator exited before READY:"; tail -40 hal_out.txt; exit 1
    fi
    sleep 1; ELAPSED=$((ELAPSED + 1))
done
if [ $ELAPSED -ge $TIMEOUT ]; then
    echo "FAIL: timed out waiting for READY"; tail -40 hal_out.txt
    kill -9 $HAL_PID || true; exit 1
fi

# Inject IRQ 7; X86Pic delivers the synthesised interrupt entry.
hal_dev_irq_trigger -i 7 || true
sleep 2

TIMEOUT=60; ELAPSED=0; SUCCESS=0
while [ $ELAPSED -lt $TIMEOUT ]; do
    if grep -q "IRQ 7 FIRED" hal_out.txt 2>/dev/null; then SUCCESS=1; break; fi
    if grep -q "X86_FAULT" hal_out.txt 2>/dev/null; then
        echo "FAIL: firmware took an unexpected fault"; break
    fi
    sleep 1; ELAPSED=$((ELAPSED + 1))
done

pkill -9 -f trigger_interrupt 2>/dev/null || true
pkill -9 -f halucinator 2>/dev/null || true
pkill -9 -f x86_irq_test 2>/dev/null || true

if [ "$SUCCESS" = "1" ]; then
    echo "X86 i386 IRQ test PASSED"; exit 0
fi
echo "FAIL: did not see IRQ 7 FIRED within ${TIMEOUT}s of injection"
echo "Last 30 lines of hal_out.txt:"; tail -30 hal_out.txt; exit 1
