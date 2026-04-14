#!/usr/bin/env bash
# Run halucinator with the p2im-drone firmware
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
cd "$SCRIPT_DIR"

# Set up project module so project.bp_handlers.* imports resolve
mkdir -p /tmp/drone_pythonpath
ln -sfn "$SCRIPT_DIR" /tmp/drone_pythonpath/project

PYTHONPATH=/tmp/drone_pythonpath PYTHONUNBUFFERED=1 halucinator \
    -c drone_config.yaml \
    -c drone_addrs.yaml \
    -c drone_memory.yaml \
    -c drone_intercepts.yaml
