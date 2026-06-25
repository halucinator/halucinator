# Copyright 2026 Christopher Wright

"""mmio_region_sampler.py -- hot-block + most-polled-MMIO histograms.

Two captures in one run, each restricted to caller-supplied address windows:
  - UC_HOOK_BLOCK histogram of hot basic-block PCs   -> "where is the CPU spinning"
  - UC_HOOK_MEM_READ histogram of MMIO read addresses -> "which device register is
    being polled in that spin"  (the dimension HAL_PC_SAMPLE lacks)
Multi-window (disjoint ranges) filtering is done in Python, which a single hook
begin/end can't express -- so you can watch e.g. the networking code AND the EMAC
window while excluding the rest of boot's noise.

Env:
  HAL_DIAG_CFGS / HAL_DIAG_SYMS / HAL_DIAG_SECS   (see _common.py)
  BLOCK_REGIONS  '0x201b0000-0x201d0000,0x20012000-0x20017000'  code windows
                 (default: all PCs)
  MMIO_REGIONS   '0xfffb0000-0xfffc0000'  MMIO windows
                 (default: 0xf0000000-0x100000000, the usual high-MMIO band)
  TOP            '20'    how many of each to print     (default 20)
  DUMP_EVERY     '0'     periodic dump every N blocks   (default 0 = on exit only)

Resolve printed PCs with your symbol csv; MMIO addrs are the device registers.
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
    block_regions = C.parse_ranges(os.environ.get("BLOCK_REGIONS", "")) or None
    mmio_regions = C.parse_ranges(os.environ.get("MMIO_REGIONS", "0xf0000000-0x100000000"))
    top = C.env_int("TOP", 20)
    dump_every = C.env_int("DUMP_EVERY", 0)
    blocks = collections.Counter()
    reads = collections.Counter()
    n = {"v": 0}

    def block_cb(u, address, size, _ud):
        if block_regions is None or C.in_ranges(address, block_regions):
            blocks[address] += 1
            n["v"] += 1
            if dump_every and n["v"] % dump_every == 0:
                _dump(blocks, reads, top)

    def read_cb(u, access, address, size, value, _ud):
        if C.in_ranges(address, mmio_regions):
            reads[address] += 1

    uc.hook_add(unicorn.UC_HOOK_BLOCK, block_cb)
    # range-gate the mem-read hook to the union span (cheap) then filter precisely
    lo = min(r[0] for r in mmio_regions)
    hi = max(r[1] for r in mmio_regions)
    uc.hook_add(unicorn.UC_HOOK_MEM_READ, read_cb, begin=lo, end=hi)
    sys.stderr.write(f"SAMPLER: block_regions={block_regions or 'ALL'} mmio_regions={mmio_regions}\n")
    import atexit
    atexit.register(lambda: _dump(blocks, reads, top))


def _dump(blocks, reads, top):
    out = ["SAMPLER hot blocks:"]
    for a, c in blocks.most_common(top):
        out.append(f"  pc 0x{a:08x}  x{c}")
    out.append("SAMPLER most-polled MMIO:")
    for a, c in reads.most_common(top):
        out.append(f"  mmio 0x{a:08x}  x{c}")
    sys.stderr.write("\n".join(out) + "\n")
    sys.stderr.flush()


if __name__ == "__main__":
    C.run(install)
