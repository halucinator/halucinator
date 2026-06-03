#!/usr/bin/env bash
# Launch halucinator with the Bus Pirate v5 (RP2040) demo configs.
#
# Designed to run alongside test/bpv5/bpv5_terminal.py (the external device
# that provides the interactive terminal): launch this in one window, then
# in another:
#
#   python3 -m test.bpv5.bpv5_terminal
#
# Or, for a non-interactive smoke test, see ./run_tests.bash which wires
# both together with a scripted device.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

# src/ for halucinator package; . so test.bpv5.bpv5_handlers is importable
export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}src:."
export PYTHONUNBUFFERED=1

EMULATOR="${HAL_EMULATOR:-avatar2}"

exec halucinator --emulator "$EMULATOR" \
    -c test/bpv5/bpv5_memory.yaml \
    -c test/bpv5/bpv5_config.yaml \
    -c test/bpv5/bpv5_addrs.yaml \
    -n bpv5
