#!/usr/bin/env bash
# Run halucinator with the p2im-drone firmware. Compiles the firmware
# from pinned upstream sources if it's not already present (the ELF/bin
# aren't checked in — see PROVENANCE.md for the license rationale).
set -euo pipefail

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
cd "$SCRIPT_DIR"

if [[ ! -f Drone.elf || ! -f Drone.bin || ! -f drone_addrs.yaml ]]; then
    echo ">>> Drone.elf / Drone.bin / drone_addrs.yaml missing — building from upstream"
    ./build_drone.sh
fi

# Set up project module so project.bp_handlers.* imports resolve
mkdir -p /tmp/drone_pythonpath
ln -sfn "$SCRIPT_DIR" /tmp/drone_pythonpath/project

PYTHONPATH=/tmp/drone_pythonpath PYTHONUNBUFFERED=1 halucinator \
    -c drone_config.yaml \
    -c drone_addrs.yaml \
    -c drone_memory.yaml \
    -c drone_intercepts.yaml
