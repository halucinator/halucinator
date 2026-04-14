#!/bin/bash
# Source this file to activate the halucinator environment:
#   source activate.sh

source ~/.virtualenvs/halucinator/bin/activate

HALUCINATOR_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export HALUCINATOR_QEMU_ARM="$HALUCINATOR_ROOT/deps/build-qemu/arm-softmmu/qemu-system-arm"
export HALUCINATOR_QEMU_ARM64="$HALUCINATOR_ROOT/deps/build-qemu/aarch64-softmmu/qemu-system-aarch64"
export HALUCINATOR_QEMU_MIPS="$HALUCINATOR_ROOT/deps/build-qemu/mips-softmmu/qemu-system-mips"
export HALUCINATOR_QEMU_PPC="$HALUCINATOR_ROOT/deps/build-qemu/ppc-softmmu/qemu-system-ppc"
export HALUCINATOR_QEMU_PPC64="$HALUCINATOR_ROOT/deps/build-qemu/ppc64-softmmu/qemu-system-ppc64"

echo "Halucinator environment activated (venv + QEMU paths set)"
