#!/bin/bash
# usage: build_qemu.sh [options] [target-list...]
#
# Options:
#   --source {avatar-qemu | libafl-qemu-bridge}
#       Pick which pre-existing fork to build from. Output goes to
#       deps/build-qemu/ (avatar-qemu, default) or deps/build-qemu-libafl/
#       (libafl-qemu-bridge).
#
#   --upstream-qemu <git-ref>
#       Clone vanilla upstream QEMU at <git-ref> into
#       deps/qemu-upstream-<ref>/, apply the avatar overlay from
#       tools/avatar-qemu-overlay/ on top, and build into
#       deps/build-qemu-upstream-<ref>/. The per-ref naming lets CI
#       (and humans) keep parallel builds for multiple upstream tags
#       — e.g. v6.2.0, v10.0.9, v11.0.0 — without one stomping on
#       another. Each build is independently cacheable.
#
# Default source tree is deps/avatar-qemu (QEMU 6.2 fork carrying the
# avatar hooks).
set -o errexit

SOURCE="avatar-qemu"
BUILD_SUBDIR="build-qemu"
UPSTREAM_REF=""

while [ "$#" -gt 0 ]; do
    case "$1" in
        --source)
            SOURCE="$2"
            case "$SOURCE" in
                avatar-qemu)         BUILD_SUBDIR="build-qemu" ;;
                libafl-qemu-bridge)  BUILD_SUBDIR="build-qemu-libafl" ;;
                *)
                    echo "build_qemu.sh: unknown --source '$SOURCE' "\
                         "(want avatar-qemu or libafl-qemu-bridge)" >&2
                    exit 1
                    ;;
            esac
            shift 2
            ;;
        --source=*)
            arg="${1#--source=}"
            exec "$0" --source "$arg" "${@:2}"
            ;;
        --upstream-qemu)
            UPSTREAM_REF="$2"
            SOURCE="upstream"
            # Suffix sanitised for filesystem use: keep alnum / dot /
            # dash / underscore (covers tags like v6.2.0, v10.0.9,
            # v11.0.0, master) and replace anything else with `_`.
            UPSTREAM_SUFFIX=$(printf '%s' "$UPSTREAM_REF" \
                | tr -c '[:alnum:]._-' '_')
            BUILD_SUBDIR="build-qemu-upstream-$UPSTREAM_SUFFIX"
            shift 2
            ;;
        --upstream-qemu=*)
            arg="${1#--upstream-qemu=}"
            exec "$0" --upstream-qemu "$arg" "${@:2}"
            ;;
        *)
            break
            ;;
    esac
done

if [ "$1" == "" ] ; then
    TARGET_LIST=("ppc-softmmu" "arm-softmmu" "aarch64-softmmu" "mips-softmmu" "ppc64-softmmu")
else
    TARGET_LIST=$@
fi

# --upstream-qemu path: clone vanilla QEMU at the given ref, apply the
# overlay from tools/avatar-qemu-overlay/, and use that as the source.
if [ -n "$UPSTREAM_REF" ]; then
    REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
    OVERLAY_DIR="$REPO_ROOT/tools/avatar-qemu-overlay"
    UPSTREAM_DIR="$REPO_ROOT/deps/qemu-upstream-$UPSTREAM_SUFFIX"

    if [ ! -d "$OVERLAY_DIR" ]; then
        echo "build_qemu.sh: overlay missing at $OVERLAY_DIR" >&2
        exit 1
    fi

    if [ ! -d "$UPSTREAM_DIR/.git" ]; then
        echo "[build_qemu.sh] cloning upstream QEMU into $UPSTREAM_DIR"
        git clone --depth 1 --branch "$UPSTREAM_REF" \
            https://gitlab.com/qemu-project/qemu.git "$UPSTREAM_DIR"
    else
        echo "[build_qemu.sh] upstream QEMU already present at $UPSTREAM_DIR"
        ( cd "$UPSTREAM_DIR" && git fetch --depth 1 origin "$UPSTREAM_REF" \
            && git checkout -q FETCH_HEAD )
    fi

    # Reset working tree so re-runs don't double-apply overlay or stale patches.
    ( cd "$UPSTREAM_DIR" && git reset --hard FETCH_HEAD 2>/dev/null \
        || git reset --hard "$UPSTREAM_REF" )
    ( cd "$UPSTREAM_DIR" && git clean -fdq -- ':!build' )

    echo "[build_qemu.sh] applying avatar overlay"
    "$OVERLAY_DIR/apply.sh" "$UPSTREAM_DIR"

    SOURCE_PATH="../../qemu-upstream-$UPSTREAM_SUFFIX"
else
    # cwd at configure time is deps/<BUILD_SUBDIR>/<target>/, so two
    # levels up reaches deps/, which is where the source trees live.
    SOURCE_PATH="../../$SOURCE"
fi

# build qemu
for target in ${TARGET_LIST[@]}
do
    pushd deps/
    mkdir -p "$BUILD_SUBDIR"/"$target"
    cd "$BUILD_SUBDIR"/"$target"
    # avatar-qemu is pinned at QEMU 6.2.0 and doesn't compile cleanly
    # against newer toolchains/libraries on its own:
    #
    #   --disable-linux-io-uring
    #     util/fdmon-io_uring.c calls io_uring_prep_poll_remove(sqe,
    #     AioHandler*) — pre-2.0 liburing took void*; ubuntu-24.04 ships
    #     liburing 2.5 which made the second arg __u64, so the call
    #     fails -Werror=int-conversion under gcc-13.
    #
    #   --disable-werror
    #     gcc-13's tightened diagnostics (dangling-pointer,
    #     array-bounds, stringop-overflow, use-after-free) fire on
    #     QEMU 6.2 sources that compiled clean on gcc-11/12. None of
    #     them are real bugs in our code path; suppress -Werror so the
    #     warnings still print but don't fail the build.
    #
    #   --disable-bpf
    #     ebpf/ebpf_rss.c calls bpf_program__set_socket_filter() —
    #     that helper was removed in libbpf 1.0+. ubuntu-24.04 ships
    #     libbpf 1.3, so the link step fails with `undefined reference`
    #     for any -softmmu target. eBPF RSS is a virtio-net offload
    #     feature halucinator doesn't use.
    #
    # Halucinator doesn't drive QEMU's block-I/O backends or eBPF RSS,
    # so all three flags are no-ops on the halucinator code path. They
    # also do nothing on ubuntu-22.04 + gcc-11 + libbpf 0.5 where the
    # build was already clean.
    #
    # libafl-qemu-bridge is on a much newer QEMU base (10.x); these
    # flags remain safe no-ops there too. The same applies to the
    # vanilla-upstream + overlay path when --upstream-qemu is used.
    "$SOURCE_PATH"/configure --target-list=$target \
        --disable-linux-io-uring --disable-werror --disable-bpf
    make all -j`nproc`
    popd
done
