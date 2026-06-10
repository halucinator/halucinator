#!/usr/bin/env bash
set -e
set -x

pkill -9 -f qemu-system-mips 2>/dev/null || true
pkill -9 -f halucinator 2>/dev/null || true
pkill -9 -f hal_dev_uart 2>/dev/null || true
pkill -9 -f hal_dev_irq_trigger 2>/dev/null || true
pkill -9 -f gdb-multiarch 2>/dev/null || true
sleep 2

rm -f ./hal_out.txt
rm -rf tmp/mips_irq_test

[ -n "${HALUCINATOR_QEMU_MIPS:-}" ] && export HALUCINATOR_QEMU_MIPS
PYTHONUNBUFFERED=1 \
    bash ./test/multi_arch_irq/mips/run.sh </dev/null \
    > hal_out.txt 2>&1 &
HAL_PID=$!

TIMEOUT=300
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

hal_dev_irq_trigger -i 5 || true
sleep 2

TIMEOUT=120
ELAPSED=0
SUCCESS=0
while [ $ELAPSED -lt $TIMEOUT ]; do
    # MIPS firmware emits "IRQ ", "5", " FIRED\n" as three separate
    # uart_write calls (gcc/-Os splits the literal output into
    # individual writes). Look for all three in order.
    if grep -q "UART TX:b'IRQ " hal_out.txt 2>/dev/null \
        && grep -q "UART TX:b'5'" hal_out.txt 2>/dev/null \
        && grep -q "UART TX:b' FIRED" hal_out.txt 2>/dev/null; then
        SUCCESS=1
        break
    fi
    if grep -q "MIPS_FAULT" hal_out.txt 2>/dev/null; then
        echo "FAIL: firmware took an unexpected exception"
        break
    fi
    sleep 1
    ELAPSED=$((ELAPSED + 1))
done

pkill -9 -f hal_dev_irq_trigger 2>/dev/null || true
pkill -9 -f halucinator 2>/dev/null || true
pkill -9 -f qemu-system-mips 2>/dev/null || true
pkill -9 -f gdb-multiarch 2>/dev/null || true

if [ "$SUCCESS" = "1" ]; then
    echo "MIPS IRQ test PASSED"
    exit 0
fi

echo "FAIL: did not see IRQ 5 FIRED within ${TIMEOUT}s of injection"
echo "Last 30 lines of hal_out.txt:"
tail -30 hal_out.txt
exit 1
