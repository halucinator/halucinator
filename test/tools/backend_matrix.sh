#!/usr/bin/env bash
# Copyright 2026 Christopher Wright

# Run the example harnesses across every backend and print a pass/fail matrix.
#
# Each cell runs in isolation with a thorough teardown (kill stale QEMU/gdb,
# wait for the GDB port to free) so back-to-back runs don't collide on the
# shared GDB port 1234 — that contention produces spurious timeouts otherwise.
#
# Usage:
#   bash test/tools/backend_matrix.sh                 # default families/backends
#   EXAMPLES="multi_arch/arm32 multi_arch/mips" \
#     BACKENDS="qemu avatar2" bash test/tools/backend_matrix.sh
#   PER_RUN_TIMEOUT=300 bash test/tools/backend_matrix.sh
#
# Run from the repo root with the halucinator venv/env active and the
# backends you want to test installed.
set -u

# Default to the portable multi_arch UART suite. The experimental per-arch
# interrupt suite (multi_arch_irq/*) is a separate framework effort — add it
# explicitly when you want it, e.g.
#   EXAMPLES="multi_arch_irq/cortex_m multi_arch_irq/x86" bash test/tools/backend_matrix.sh
EXAMPLES="${EXAMPLES:-
multi_arch/arm32 multi_arch/arm64 multi_arch/mips multi_arch/ppc multi_arch/ppc64
}"
BACKENDS="${BACKENDS:-unicorn avatar2 qemu renode ghidra libafl-qemu}"
PER_RUN_TIMEOUT="${PER_RUN_TIMEOUT:-300}"
LOGDIR="${LOGDIR:-/tmp/hal_backend_matrix}"
mkdir -p "$LOGDIR"

teardown() {
    pkill -9 -f qemu-system          2>/dev/null
    pkill -9 -f gdb-multiarch         2>/dev/null
    pkill -9 -f 'halucinator --emulator' 2>/dev/null
    pkill -9 -f ioserver              2>/dev/null
    # wait for the internal GDB port to free before the next run
    for _ in $(seq 1 25); do
        ss -ltn 2>/dev/null | grep -q ':1234 ' || break
        sleep 1
    done
    sleep 1
}

printf '%-26s %-12s %s\n' EXAMPLE BACKEND RESULT
for ex in $EXAMPLES; do
    for be in $BACKENDS; do
        teardown
        tag="${ex//\//_}__${be}"
        HAL_EMULATOR="$be" timeout "$PER_RUN_TIMEOUT" \
            bash "test/${ex}/run_tests.bash" >"$LOGDIR/$tag.out" 2>&1
        rc=$?
        case $rc in
            0)   v=PASS ;;
            124) v=TIMEOUT ;;
            *)   v="FAIL($rc)" ;;
        esac
        printf '%-26s %-12s %s\n' "$ex" "$be" "$v"
    done
done
teardown
