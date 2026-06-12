# Copyright 2026 Christopher Wright

"""caller_histogram.py -- who keeps calling this function? (loop-driver finder)

At a chosen function ENTRY, record the link register (return address) every time
it fires. The top callers reveal which loop is driving a blocking/hot function
(e.g. the caller of a semTake, or of a per-cycle dispatch that spins). This is the
INVERSE of HAL_CALL_TRACE (which records callees keyed by caller).

Env:
  HAL_DIAG_CFGS / HAL_DIAG_SYMS / HAL_DIAG_SECS   (see _common.py)
  TARGET_PC   '0x20054d80'   function entry to watch (address-filtered hook)
  LR_LO/LR_HI '0x20000000'   optional: only record LRs within [LR_LO,LR_HI)
  MODE        hist|seq        histogram of callers, or rolling last-N sequence
  SEQ_N       '64'            window size for MODE=seq (default 64)
  OUT         path            also write the dump here (default: stderr only)
  DUMP_EVERY  '200000'        emit the running top-10 every N hits (default 0=off)

Resolve the printed LR addresses to function names with your symbol csv.

Example (find the loop driving ComExecCPU::Out):
  HAL_DIAG_CFGS=cfg.yaml TARGET_PC=0x20054d80 MODE=hist python3 caller_histogram.py
"""
import collections
import os
import sys

try:
    from halucinator.diagnostics import _common as C
except ImportError:  # direct-path execution
    import _common as C
import unicorn


def install(backend):
    uc = backend._uc
    target = int(os.environ["TARGET_PC"], 0)
    lr_lo = C.env_int("LR_LO", 0)
    lr_hi = C.env_int("LR_HI", 0xFFFFFFFF)
    mode = os.environ.get("MODE", "hist")
    seq_n = C.env_int("SEQ_N", 64)
    out_path = os.environ.get("OUT")
    dump_every = C.env_int("DUMP_EVERY", 0)
    hist = collections.Counter()
    seq = collections.deque(maxlen=seq_n)
    n = {"v": 0}

    def cb(u, address, size, _ud):
        lr = u.reg_read(C.ARM_REGS["lr"]) & ~1
        if not (lr_lo <= lr < lr_hi):
            return
        n["v"] += 1
        if mode == "seq":
            seq.append(lr)
        else:
            hist[lr] += 1
        if dump_every and n["v"] % dump_every == 0:
            _dump(hist, seq, mode, out_path)

    uc.hook_add(unicorn.UC_HOOK_CODE, cb, begin=target, end=target)
    sys.stderr.write(f"CALLERS: watching 0x{target:08x} (mode={mode})\n")
    # dump on watchdog exit too
    import atexit
    atexit.register(lambda: _dump(hist, seq, mode, out_path))


def _dump(hist, seq, mode, out_path):
    lines = []
    if mode == "seq":
        lines.append("CALLERS seq (oldest->newest): " + " -> ".join(f"0x{x:08x}" for x in seq))
    else:
        lines.append("CALLERS top:")
        for lr, c in hist.most_common(15):
            lines.append(f"  0x{lr:08x}  x{c}")
    text = "\n".join(lines) + "\n"
    sys.stderr.write(text)
    sys.stderr.flush()
    if out_path:
        with open(out_path, "w") as f:
            f.write(text)


if __name__ == "__main__":
    C.run(install)
