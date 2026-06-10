#!/usr/bin/env bash
# End-to-end IRQ delivery test for the Cortex-M3 firmware.
#
# Boots halucinator with the test_irq firmware, waits for READY in
# the UART output, then triggers IRQ #17 via hal_dev_irq_trigger.
# The firmware's ISR sets a flag; main() prints "IRQ 17 FIRED" and
# halts. Test passes if both READY and "IRQ 17 FIRED" appear.

set -e
set -x

# Clean up any leftover processes from previous tests.
pkill -9 -f qemu-system-arm 2>/dev/null || true
pkill -9 -f halucinator 2>/dev/null || true
pkill -9 -f hal_dev_uart 2>/dev/null || true
pkill -9 -f hal_dev_irq_trigger 2>/dev/null || true
pkill -9 -f gdb-multiarch 2>/dev/null || true
sleep 2

rm -f ./hal_out.txt
rm -rf tmp/cortex_m_irq_test

# Launch halucinator. Backend selected via $HAL_EMULATOR (default
# avatar2). The matrix harness sets this per cell. Pass through
# HALUCINATOR_QEMU_ARM only when caller actually set it - empty string
# triggers halucinator's "Path ENV VAR is invalid" check.
[ -n "${HALUCINATOR_QEMU_ARM:-}" ] && export HALUCINATOR_QEMU_ARM
PYTHONUNBUFFERED=1 \
    bash ./test/multi_arch_irq/cortex_m/run.sh </dev/null \
    > hal_out.txt 2>&1 &
HAL_PID=$!

# Wait up to 60s for the firmware to print READY (long enough for
# QEMU + GDB + halucinator init on a slow CI runner).
TIMEOUT=60
ELAPSED=0
while [ $ELAPSED -lt $TIMEOUT ]; do
    if grep -q "UART TX:b'READY" hal_out.txt 2>/dev/null; then
        echo "Firmware reached READY after ${ELAPSED}s"
        break
    fi
    if ! kill -0 $HAL_PID 2>/dev/null; then
        echo "halucinator exited before READY:"
        tail -40 hal_out.txt
        exit 1
    fi
    sleep 1
    ELAPSED=$((ELAPSED + 1))
done

if [ $ELAPSED -ge $TIMEOUT ]; then
    echo "FAIL: timed out waiting for READY"
    tail -40 hal_out.txt
    kill -9 $HAL_PID || true
    exit 1
fi

# Inject IRQ #17. The firmware enabled IRQ #17 in NVIC_ISER0 before
# entering its polling loop, so the asserted line should pre-empt
# main() and run IRQ_Handler.
hal_dev_irq_trigger -i 17 || true
sleep 2

# Wait up to 30s for the firmware to log the FIRED sentinel.
TIMEOUT=30
ELAPSED=0
SUCCESS=0
while [ $ELAPSED -lt $TIMEOUT ]; do
    if grep -q "IRQ 17 FIRED" hal_out.txt 2>/dev/null; then
        SUCCESS=1
        break
    fi
    if grep -q "TIMEOUT" hal_out.txt 2>/dev/null; then
        break
    fi
    if grep -q "HARDFAULT" hal_out.txt 2>/dev/null; then
        echo "FAIL: firmware took a HardFault"
        break
    fi
    sleep 1
    ELAPSED=$((ELAPSED + 1))
done

# Tear down.
pkill -9 -f hal_dev_irq_trigger 2>/dev/null || true
pkill -9 -f halucinator 2>/dev/null || true
pkill -9 -f qemu-system-arm 2>/dev/null || true
pkill -9 -f gdb-multiarch 2>/dev/null || true

if [ "$SUCCESS" = "1" ]; then
    echo "Cortex-M IRQ test PASSED"
    exit 0
fi

echo "FAIL: did not see IRQ 17 FIRED within ${TIMEOUT}s of injection"
echo "Last 30 lines of hal_out.txt:"
tail -30 hal_out.txt
exit 1
