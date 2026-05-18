#!/bin/bash
# Apply the avatar/halucinator QEMU overlay to a vanilla upstream QEMU
# source tree.
#
# usage:
#   tools/avatar-qemu-overlay/apply.sh <upstream-qemu-src-dir>
#
# What it does:
#   1. Copies hw/avatar/* and qapi/avatar-target.json into the target
#      source tree (purely additive — no mainline files touched).
#   2. Applies the curated mainline-hook patches in patches/*.patch in
#      lexical order (currently a single combined patch).
#
# The result is a source tree functionally equivalent to avatar-qemu
# for halucinator's purposes — same `configurable` machine, same
# `avatar-rmemory` device, same QMP commands — but on top of whatever
# QEMU release the user supplied.
#
# Verified against QEMU v6.2.0. Newer releases may need patch refresh;
# the patches/*.patch files are short and self-contained, so a
# refresh is mostly a 3-way merge of header layouts that shifted.
set -o errexit

usage() {
    echo "usage: $0 <upstream-qemu-src-dir>" >&2
    exit 2
}

if [ $# -ne 1 ]; then
    usage
fi

QEMU_SRC=$1
if [ ! -d "$QEMU_SRC" ]; then
    echo "$0: '$QEMU_SRC' is not a directory" >&2
    exit 1
fi
if [ ! -f "$QEMU_SRC/configure" ] || [ ! -d "$QEMU_SRC/hw" ]; then
    echo "$0: '$QEMU_SRC' doesn't look like a QEMU source tree" >&2
    exit 1
fi

OVERLAY_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "[apply.sh] copying additive overlay into $QEMU_SRC"
mkdir -p "$QEMU_SRC/hw/avatar"
cp -f "$OVERLAY_DIR"/hw/avatar/* "$QEMU_SRC/hw/avatar/"
cp -f "$OVERLAY_DIR"/qapi/avatar-target.json "$QEMU_SRC/qapi/"

echo "[apply.sh] applying mainline-hook patches"
for p in "$OVERLAY_DIR"/patches/*.patch; do
    [ -e "$p" ] || continue   # empty patches/ dir is fine
    echo "  -> $(basename "$p")"
    ( cd "$QEMU_SRC" && git apply --check "$p" 2>&1 ) || {
        echo "[apply.sh] patch did not apply cleanly: $p" >&2
        echo "[apply.sh] this usually means the upstream QEMU version" >&2
        echo "[apply.sh] is too new (or too old) for this patch. The" >&2
        echo "[apply.sh] failure context above shows which mainline" >&2
        echo "[apply.sh] file shifted — typically a 3-way merge fixes" >&2
        echo "[apply.sh] it. Verified clean against QEMU v6.2.0." >&2
        exit 1
    }
    ( cd "$QEMU_SRC" && git apply "$p" )
done

echo "[apply.sh] overlay applied"
echo "          source tree is now ready: cd <build-dir> &&"
echo "          $QEMU_SRC/configure --target-list=arm-softmmu \\"
echo "              --disable-werror --disable-bpf --disable-linux-io-uring"
