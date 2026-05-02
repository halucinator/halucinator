#!/usr/bin/env bash
# Run every supported firmware × backend cell. Emits a pass/fail/skip
# matrix line per cell.
#
# Usage: ./run_backend_matrix.sh
#
# Each cell runs in a fresh Docker container with a hard timeout, captures
# stdout into /tmp, and classifies the outcome based on sentinel strings.
# A cell is PASS if its firmware-specific expected pattern is present and
# no fatal errors are logged. Cells that need host-side support the local
# image doesn't ship (e.g. Renode without a MIPS CPU class) are SKIP.
#
# Bash 3.2 portable — runs on macOS's stock /bin/bash. Self-contained:
# the halucinator-shim wrapper used to inject `--emulator $EMU` into the
# legacy run.sh scripts is generated to a tmp file at startup.

set -u
DOCKER=${DOCKER:-/Applications/Docker.app/Contents/Resources/bin/docker}
IMAGE=${IMAGE:-halucinator:ghidra}
SRC=$(pwd)

BACKENDS=(avatar2 qemu renode unicorn ghidra)

# Per-firmware: label, working-dir, expected-pass grep, wait-seconds.
# WORKDIR is the cwd inside the container; the matching RUNCMD below
# uses paths interpreted relative to that cwd.
FIRMWARES=(
  "STM32_Hyperterminal|/root/halucinator|UART 1073811456 TX|25"
  "zephyr_frdm_k64f|/root/halucinator/test/zephyr/frdm_k64f_UART_Excellent_Test|uart_mcux_init Called|25"
  "zephyr_olimex_h103|/root/halucinator/test/zephyr/olimex_stm32_h103_UART_Excellent_test|HAL_LOG|25"
  "zephyr_fs|/root/halucinator/test/zephyr/zephyr_fs|HAL_LOG|25"
  "p2im_drone|/root/halucinator|HAL_LOG|25"
  "multi_arch_arm32|/root/halucinator|UART.*TX|25"
  "multi_arch_arm64|/root/halucinator|UART.*TX|25"
  "multi_arch_mips|/root/halucinator|UART.*TX|25"
  "multi_arch_ppc|/root/halucinator|UART.*TX|25"
  "multi_arch_ppc64|/root/halucinator|UART.*TX|35"
)

# firmware/backend pairs the local image can't run end-to-end. Reported
# as SKIP rather than FAIL so the matrix is honest about combinations
# the host setup doesn't cover.
#   STM32_Hyperterminal/renode  – needs Renode's stm32f4 board file (RCC
#                                 bit emulation), not just a flat memory
#                                 stub. The bp_handler intercepts skip
#                                 the HAL functions but the firmware
#                                 still touches RCC registers in between.
#   multi_arch_mips/renode      – the linux-arm64-dotnet-portable Renode
#                                 ships no MIPS CPU class
#                                 (CPU.MIPS / MIPSCpu / MIPS4Kc all fail
#                                 to resolve at LoadPlatformDescription).
#   multi_arch_ppc64/qemu       – the avatar-qemu fork's ppc64 gdbstub
#                                 asserts in handle_read_all_regs the
#                                 first time the 'g' packet is sent; the
#                                 backend works around that by using 'p'
#                                 reads only, but 'P' writes return empty
#                                 too (unsupported), so PC/SP can never be
#                                 set and the firmware never enters its
#                                 entry point. Real fix needs a patch to
#                                 deps/avatar-qemu and a QEMU rebuild.
#   multi_arch_ppc64/unicorn    – unicorn-engine 2.0.1's ppc64 model
#                                 raises CPU exception 70 within the
#                                 first 30 instructions; the firmware
#                                 never reaches uart_write. PPC64 Book3S
#                                 support in unicorn is incomplete.
SKIP_PAIRS=(
  "STM32_Hyperterminal/renode"
  "multi_arch_mips/renode"
  "multi_arch_ppc64/qemu"
  "multi_arch_ppc64/unicorn"
)

is_skip() {
  local pair="$1" s
  for s in "${SKIP_PAIRS[@]}"; do
    [ "$s" = "$pair" ] && return 0
  done
  return 1
}

# Generate the in-container halucinator wrapper. Several legacy run.sh
# scripts (STM32, all zephyr targets, p2im_drone) call `halucinator …`
# without --emulator, so HAL_EMULATOR=… alone wouldn't switch backends.
# The shim injects --emulator $HAL_EMULATOR_INJECT iff the caller didn't
# pass one.
SHIM=$(mktemp -t halucinator-shim.XXXXXX.sh)
trap 'rm -f "$SHIM"' EXIT
cat > "$SHIM" <<'SHIM_EOF'
#!/usr/bin/env bash
set -u
REAL="${HAL_REAL_BIN:-/usr/local/bin/_halucinator_real}"
for a in "$@"; do
  case "$a" in
    --emulator|--emulator=*) exec "$REAL" "$@" ;;
  esac
done
exec "$REAL" --emulator "${HAL_EMULATOR_INJECT:-avatar2}" "$@"
SHIM_EOF
chmod +x "$SHIM"

KEYS=()
VALS=()

mkdir -p .matrix_logs
LOG_MOUNT="$SRC/.matrix_logs"

for FW_SPEC in "${FIRMWARES[@]}"; do
  IFS='|' read -r NAME DIR PAT WAIT <<< "$FW_SPEC"
  case "$NAME" in
    STM32_Hyperterminal)    RUNCMD="bash test/STM32/example/run.sh" ;;
    zephyr_frdm_k64f|zephyr_olimex_h103|zephyr_fs) RUNCMD="bash run.sh" ;;
    p2im_drone)             RUNCMD="bash test/firmware-rehosting/p2im-drone/run.sh" ;;
    multi_arch_*)           ARCH="${NAME#multi_arch_}" ; RUNCMD="bash test/multi_arch/$ARCH/run.sh" ;;
  esac
  for EMU in "${BACKENDS[@]}"; do
    LOG=".matrix_logs/${NAME}__${EMU}.log"
    KEY="$NAME/$EMU"
    if is_skip "$KEY"; then
      echo ">>> $KEY (skip)"
      KEYS+=("$KEY")
      VALS+=("SKIP")
      continue
    fi
    echo ">>> $KEY"
    "$DOCKER" run --rm \
      -v "$SRC/src:/root/halucinator/src" \
      -v "$SRC/test:/root/halucinator/test" \
      -v "$LOG_MOUNT:/root/halucinator/.matrix_logs" \
      -v "$SHIM:/usr/local/bin/_halucinator_shim.sh:ro" \
      -w "$DIR" "$IMAGE" bash -c "
        set +e
        if [ ! -f /usr/local/bin/_halucinator_real ]; then
          cp \$(which halucinator) /usr/local/bin/_halucinator_real
        fi
        cp /usr/local/bin/_halucinator_shim.sh /usr/local/bin/halucinator
        chmod +x /usr/local/bin/halucinator
        export HAL_EMULATOR_INJECT=$EMU
        export HAL_REAL_BIN=/usr/local/bin/_halucinator_real
        export HAL_EMULATOR=$EMU
        export GHIDRA_INSTALL_DIR=/opt/ghidra
        rm -rf tmp /tmp/tmp
        timeout ${WAIT} $RUNCMD </dev/null >/tmp/hal_out.txt 2>&1 &
        HAL_PID=\$!
        sleep $((WAIT - 2))
        cp /tmp/hal_out.txt /root/halucinator/.matrix_logs/${NAME}__${EMU}.log 2>/dev/null || true
        kill \$HAL_PID 2>/dev/null
        pkill -9 -f renode 2>/dev/null
        pkill -9 -f qemu 2>/dev/null
        true
      " > /dev/null 2>&1
    if [[ -s "$LOG" ]]; then
      if grep -qE "Traceback|cpu abort|unhandled exception|FATAL" "$LOG"; then
        VAL="FAIL"
      elif grep -qE "$PAT" "$LOG"; then
        VAL="PASS"
      else
        VAL="NO-OUTPUT"
      fi
    else
      VAL="NO-LOG"
    fi
    KEYS+=("$KEY")
    VALS+=("$VAL")
    echo "    $KEY -> $VAL"
  done
done

lookup() {
  local target="$1" i=0
  while [ $i -lt ${#KEYS[@]} ]; do
    if [ "${KEYS[$i]}" = "$target" ]; then
      echo "${VALS[$i]}"
      return
    fi
    i=$((i + 1))
  done
  echo "?"
}

echo
echo "============================= SUMMARY ============================="
printf "%-24s" "firmware"
for EMU in "${BACKENDS[@]}"; do printf "%-12s" "$EMU"; done
echo
fail=0
for FW_SPEC in "${FIRMWARES[@]}"; do
  IFS='|' read -r NAME _ _ _ <<< "$FW_SPEC"
  printf "%-24s" "$NAME"
  for EMU in "${BACKENDS[@]}"; do
    v=$(lookup "$NAME/$EMU")
    printf "%-12s" "$v"
    case "$v" in
      PASS|SKIP) ;;
      *) fail=1 ;;
    esac
  done
  echo
done

exit $fail
