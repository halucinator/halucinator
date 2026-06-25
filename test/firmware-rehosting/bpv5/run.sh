#!/usr/bin/env bash
# Copyright 2026 Christopher Wright

# Launch halucinator with the Bus Pirate v5 (RP2040) demo configs.
#
# Designed to run alongside test/firmware-rehosting/bpv5/bpv5_terminal.py (the external device
# that provides the interactive terminal): launch this in one window, then
# in another:
#
#   python3 -m halucinator.external_devices.bpv5_terminal
#
# Or, for a non-interactive smoke test, see ./run_tests.bash which wires
# both together with a scripted device.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$REPO_ROOT"

# src/ for halucinator package; . so halucinator.external_devices.bpv5_terminal is importable
export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}src:."
export PYTHONUNBUFFERED=1

# Default backend. avatar2/qemu can't run on macOS (Linux qemu binaries),
# so honour an explicit HAL_EMULATOR but fall back to unicorn on Darwin.
if [[ -n "$HAL_EMULATOR" ]]; then
    EMULATOR="$HAL_EMULATOR"
elif [[ "$(uname -s)" == "Darwin" ]]; then
    EMULATOR="unicorn"
else
    EMULATOR="unicorn"
fi

exec halucinator --emulator "$EMULATOR" \
    -c test/firmware-rehosting/bpv5/bpv5_memory.yaml \
    -c test/firmware-rehosting/bpv5/bpv5_config.yaml \
    -c test/firmware-rehosting/bpv5/bpv5_addrs.yaml \
    -n bpv5
