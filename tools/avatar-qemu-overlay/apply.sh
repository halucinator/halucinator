#!/bin/bash
# Apply the avatar/halucinator QEMU overlay to a vanilla upstream QEMU
# source tree.
#
# usage:
#   tools/avatar-qemu-overlay/apply.sh <upstream-qemu-src-dir> [version]
#
# Picks a version-specific subdirectory of the overlay based on the
# upstream QEMU's VERSION file (or the optional explicit *version*
# argument). Currently shipped subdirs:
#
#   v6.2.x/   verified against QEMU 6.2.0 (matches the avatar-qemu fork
#             our default `qemu` backend tracks).
#   v10.x/    verified against QEMU 10.0.3 (matches libafl-qemu-bridge;
#             the avatar layer's API surface shifted between 6.2 and
#             10 enough that a separate variant is cleaner than one
#             with #ifdef walls).
#
# What it does for each version:
#   1. Copies the version's hw/avatar/, qapi/avatar-target.json, and
#      (if present) include/hw/avatar/ headers into the target source
#      tree. Purely additive — no mainline files touched in this step.
#   2. Applies the version's patches/*.patch in lexical order — the
#      small set of mainline-file hooks (machine class, NVIC IRQ
#      hook, build-system glue, log mask).
#
# Forward-port to a new QEMU release = create a new version subdir,
# refresh the mainline patches against the new base, smoke-test it
# builds. The header + .c files usually only need cosmetic updates
# since they live in their own hw/avatar/ namespace.
set -o errexit

usage() {
    echo "usage: $0 <upstream-qemu-src-dir> [version-subdir]" >&2
    echo "  version-subdir defaults to v6.2.x if VERSION starts with 6.," >&2
    echo "  v10.x if VERSION starts with 10. or 11.." >&2
    exit 2
}

if [ $# -lt 1 ] || [ $# -gt 2 ]; then
    usage
fi

QEMU_SRC=$1
EXPLICIT_VARIANT=${2:-}
if [ ! -d "$QEMU_SRC" ]; then
    echo "$0: '$QEMU_SRC' is not a directory" >&2
    exit 1
fi
if [ ! -f "$QEMU_SRC/configure" ] || [ ! -d "$QEMU_SRC/hw" ]; then
    echo "$0: '$QEMU_SRC' doesn't look like a QEMU source tree" >&2
    exit 1
fi

OVERLAY_DIR="$(cd "$(dirname "$0")" && pwd)"

# Pick the version subdir.
if [ -n "$EXPLICIT_VARIANT" ]; then
    VARIANT="$EXPLICIT_VARIANT"
else
    QEMU_VER=""
    if [ -f "$QEMU_SRC/VERSION" ]; then
        QEMU_VER=$(head -n1 "$QEMU_SRC/VERSION" | tr -d ' \t\n')
    fi
    case "$QEMU_VER" in
        6.*)        VARIANT="v6.2.x"  ;;
        10.*|11.*)  VARIANT="v10.x"   ;;
        *)
            echo "[apply.sh] unrecognised QEMU VERSION '$QEMU_VER';" >&2
            echo "[apply.sh] pass an explicit variant subdir as the 2nd arg," >&2
            echo "[apply.sh] e.g. '$0 $QEMU_SRC v10.x'." >&2
            echo "[apply.sh] Available: $(ls -d "$OVERLAY_DIR"/v* 2>/dev/null | xargs -n1 basename | tr '\n' ' ')" >&2
            exit 1
            ;;
    esac
fi

VARIANT_DIR="$OVERLAY_DIR/$VARIANT"
if [ ! -d "$VARIANT_DIR" ]; then
    echo "[apply.sh] no overlay variant at '$VARIANT_DIR'" >&2
    exit 1
fi

echo "[apply.sh] target: $QEMU_SRC  variant: $VARIANT"

echo "[apply.sh] copying additive overlay"
mkdir -p "$QEMU_SRC/hw/avatar" "$QEMU_SRC/qapi"
cp -f "$VARIANT_DIR"/hw/avatar/* "$QEMU_SRC/hw/avatar/"
cp -f "$VARIANT_DIR"/qapi/avatar-target.json "$QEMU_SRC/qapi/"
if [ -d "$VARIANT_DIR/include/hw/avatar" ]; then
    mkdir -p "$QEMU_SRC/include/hw/avatar"
    cp -f "$VARIANT_DIR"/include/hw/avatar/* "$QEMU_SRC/include/hw/avatar/"
fi

echo "[apply.sh] applying mainline-hook patches"
for p in "$VARIANT_DIR"/patches/*.patch; do
    [ -e "$p" ] || continue   # empty patches/ dir is fine
    echo "  -> $(basename "$p")"
    # Try strict `git apply` first; if context shifted (e.g. a newer
    # point release added a line nearby), fall back to GNU `patch` with
    # fuzz 3. This trades strictness for the ability to absorb minor
    # upstream churn between point releases without forking the patch.
    if ( cd "$QEMU_SRC" && git apply --check "$p" >/dev/null 2>&1 ); then
        ( cd "$QEMU_SRC" && git apply "$p" )
    else
        echo "    (git apply rejected; falling back to patch -N -F3)"
        # `patch -N` skips hunks already merged upstream (matters when
        # the same overlay covers multiple QEMU point/major releases —
        # e.g. v10.0.3 + v11.0.0 where v11 picked up one of our hunks
        # natively). Apple/BSD patch also prompts "Assume -R?" with
        # default yes on EOF, which would silently *un-install* the
        # upstream version of the hunk; `yes n |` answers no. Reading
        # via `-i` frees stdin for that.
        #
        # patch exits 1 whenever any hunk is skipped — even when -N
        # treats the skip as "already applied, fine". So we can't
        # trust the exit code, and a leftover .rej file can be either
        # a genuine rejection OR the saved copy of an
        # already-applied-skipped hunk. We distinguish via patch's own
        # stdout: "hunks FAILED" / "FAILED at" indicate a real miss,
        # while "Ignoring previously applied" + .rej is just the
        # already-applied case and the target file is already at the
        # goal state.
        log=$(mktemp)
        ( cd "$QEMU_SRC" && yes n 2>/dev/null \
            | patch -p1 -N -F3 -i "$p" 2>&1 ) > "$log" || true
        if grep -qE 'hunks? FAILED|FAILED at' "$log"; then
            echo "[apply.sh] patch did not apply cleanly: $p" >&2
            cat "$log" >&2
            find "$QEMU_SRC" -name '*.rej' -print -exec cat {} \; >&2
            find "$QEMU_SRC" \( -name '*.rej' -o -name '*.orig' \) -delete
            rm -f "$log"
            echo "[apply.sh] the upstream QEMU version may be too new" >&2
            echo "[apply.sh] (or too old) for this variant — try a" >&2
            echo "[apply.sh] different variant subdir, or refresh." >&2
            exit 1
        fi
        # No real failures — print a one-line summary of what got
        # skipped so the log makes it obvious what's redundant.
        grep -E 'Ignoring previously applied|previously reversed' "$log" \
            | sed 's/^/    /' || true
        find "$QEMU_SRC" \( -name '*.rej' -o -name '*.orig' \) -delete
        rm -f "$log"
    fi
done

echo "[apply.sh] overlay applied"
echo "          source tree is now ready: cd <build-dir> &&"
echo "          $QEMU_SRC/configure --target-list=arm-softmmu \\"
echo "              --disable-werror --disable-bpf --disable-linux-io-uring \\"
echo "              --enable-avatar"
