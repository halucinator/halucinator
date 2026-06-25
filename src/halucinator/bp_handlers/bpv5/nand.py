# Copyright 2026 Christopher Wright

"""Bus Pirate v5 — modeled on-board SPI-NAND storage (FatFs-level HLE).

The real storage stack is:

    FatFs (``f_open``/``f_read``/``f_readdir``/``f_gets`` …)
      -> dhara (flash translation layer)
        -> ``spi_nand_*`` (page read/program/erase)
          -> PL022 SSP (the shared SPI controller)

Emulating the whole NAND + dhara + FatFs chain at the block level is heavy and
buys nothing for a "storage works" proof. Instead we HLE at the **highest clean
seam — the FatFs ABI** — and serve a small *modeled* filesystem from Python.
The firmware's own ``storage_ls`` / ``disk_cat_handler`` print loops then render
our entries, so the listing and file contents the CLI shows are genuinely the
bytes this model supplies, flowing through the live firmware.

Two FatFs surfaces are intercepted (all addresses RE'd from
``bus_pirate5_rev10.bin`` against ``bpv5_addrs.yaml``; flash base 0x10000000):

* **mount** — ``f_mount(FATFS*, path, opt)`` returns ``FR_OK`` (0) so
  ``storage_mount`` reports the volume mounted during boot. (Replaces the
  boot-time ``skip_storage_mount`` SkipFunc, which faked success without ever
  exercising FatFs.)

* **directory listing** (the CLI ``ls`` → ``disk_ls_handler`` → ``storage_ls``):
    - ``f_opendir(DIR*, path)``  -> FR_OK, (re)start our entry iterator.
    - ``f_readdir(DIR*, FILINFO*)`` -> FR_OK; fill the caller's FILINFO with the
      next modeled entry. FatFs ``FF_USE_LFN==0`` 8.3 layout (proven from
      ``get_fileinfo``): ``fsize`` @ +0 (DWORD), ``fattrib`` @ +8 (BYTE;
      0x10=AM_DIR, 0x20=AM_ARC), ``fname`` @ +9 (8.3, NUL-terminated). After
      the last entry we write ``fname[0]=0`` — the FatFs end-of-dir signal
      ``storage_ls`` loops on.
    - ``f_closedir(DIR*)`` -> FR_OK.

* **file read** (the CLI ``cat <name>`` → ``disk_cat_handler``):
    - ``f_open(FIL*, path, mode)`` -> FR_OK if the modeled file exists (else
      FR_NO_FILE=4); we stash the file's line iterator keyed by the FIL* ptr.
    - ``f_gets(char* buf, int len, FIL*)`` -> copy the next line into ``buf`` and
      return ``buf``; return NULL (0) at EOF. (``disk_cat_handler`` prints each
      returned line; ``f_gets`` is the leaf the firmware actually calls.)
    - ``f_read(FIL*, buf, btr, &br)`` -> serve raw bytes (used by
      ``storage_load_config`` reading ``bpconfig.bp``); FR_OK + ``*br`` set.
    - ``f_close(FIL*)`` -> FR_OK, drop the iterator.

Every served op is logged ``[Storage] ...`` so the run captures exactly what the
model handed the firmware.

Handlers annotate ``HalBackend`` (the abstract base), so they work on unicorn
(macOS) and avatar2 alike via ``get_arg`` / ``read_memory`` / ``write_memory``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Tuple

from halucinator.bp_handlers.bp_handler import BPHandler, bp_handler

if TYPE_CHECKING:
    from halucinator.backends.hal_backend import HalBackend


# FatFs FRESULT codes (subset).
FR_OK = 0
FR_NO_FILE = 4

# FILINFO field offsets — FatFs FF_USE_LFN==0, FF_FS_EXFAT==0 (8.3 short name).
# Proven from get_fileinfo @ 0x1002b374 (strb [r1,#9] name / strb [r1,#8]
# fattrib / str [r1,#0] fsize). fname buffer is 13 bytes (8.3 + dot + NUL).
FINFO_FSIZE = 0
FINFO_FATTRIB = 8
FINFO_FNAME = 9
FINFO_FNAME_MAX = 13

# fattrib bits (FatFs).
AM_RDO = 0x01
AM_ARC = 0x20
AM_DIR = 0x10


def _read_cstr(qemu: "HalBackend", ptr: int, maxlen: int = 64) -> str:
    """Read a NUL-terminated ASCII string from guest memory."""
    if not ptr:
        return ""
    raw = qemu.read_memory(ptr, 1, maxlen, raw=True)
    nul = raw.find(b"\x00")
    if nul >= 0:
        raw = raw[:nul]
    return raw.decode("latin1")


class NandStorage(BPHandler):
    """A modeled FatFs volume served at the FatFs ABI seam.

    The default image is a tiny but realistic Bus Pirate config volume: a
    ``bpconfig.bp`` JSON config, a couple of ``.scr`` macro scripts, a README
    and a ``logs`` subdirectory. Override via ``registration_args``:
    ``files`` (``{name: str|bytes}``) and ``dirs`` (``[name, ...]``).
    """

    DEFAULT_FILES: Dict[str, bytes] = {
        "bpconfig.bp": (
            b'{\r\n'
            b'  "terminal_language": 0,\r\n'
            b'  "terminal_ansi_color": 1,\r\n'
            b'  "terminal_ansi_rows": 24,\r\n'
            b'  "terminal_ansi_columns": 80,\r\n'
            b'  "led_brightness": 10\r\n'
            b'}\r\n'
        ),
        "hello.scr": (
            b'# modeled startup macro\r\n'
            b'm 1\r\n'
            b'[0x9f r:3]\r\n'
        ),
        "readme.txt": (
            b'Bus Pirate v5 on-board SPI-NAND storage (MODELED via HALucinator).\r\n'
            b'These bytes are served from the NandStorage FatFs HLE handler.\r\n'
        ),
    }
    DEFAULT_DIRS: List[str] = ["logs"]

    def __init__(self, files=None, dirs=None) -> None:
        super().__init__()
        src = self.DEFAULT_FILES if files is None else files
        self.files: Dict[str, bytes] = {}
        for name, content in src.items():
            if isinstance(content, str):
                content = content.encode("latin1")
            self.files[name] = bytes(content)
        self.dirs: List[str] = list(self.DEFAULT_DIRS if dirs is None else dirs)

        # Directory iteration state (one dir read at a time, as storage_ls does).
        self._dir_entries: List[Tuple[str, int, int]] = []  # (name, fsize, fattrib)
        self._dir_idx = 0

        # Open-file state keyed by the guest FIL* pointer: {fp: {"name","lines","lpos","rpos"}}.
        self._open: Dict[int, dict] = {}

        print(
            "[Storage] modeled SPI-NAND FatFs volume attached: "
            f"files={sorted(self.files)} dirs={self.dirs}",
            flush=True,
        )

    # --- mount ----------------------------------------------------------
    @bp_handler(["f_mount"])
    def f_mount(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``FRESULT f_mount(FATFS* fs, const TCHAR* path, BYTE opt)`` -> FR_OK.

        ``storage_mount`` reads three BPB fields off the FATFS object on the
        success path to display the volume capacity: ``fs_type`` @ +0,
        ``csize`` (sectors/cluster) @ +0xa, and the cluster count @ +0x20
        (capacity = csize * clusters << 11). The real f_mount (skipped at the
        NAND/dhara level) never populated them, so seed sane values to render a
        believable size instead of 0.
        """
        fs = qemu.get_arg(0)
        if fs:
            qemu.write_memory(fs + 0x00, 1, 3)          # fs_type = FS_FAT32
            qemu.write_memory(fs + 0x0A, 2, 1)          # csize = 1 sector/cluster
            qemu.write_memory(fs + 0x20, 4, 0x00010000)  # n_fatent -> 128 MiB
        print("[Storage] f_mount() -> FR_OK (volume mounted)", flush=True)
        return True, FR_OK

    # --- directory listing (ls) -----------------------------------------
    def _build_dir(self) -> None:
        entries: List[Tuple[str, int, int]] = []
        for d in self.dirs:
            entries.append((d, 0, AM_DIR))
        for name, content in self.files.items():
            entries.append((name, len(content), AM_ARC))
        self._dir_entries = entries
        self._dir_idx = 0

    @bp_handler(["f_opendir"])
    def f_opendir(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``FRESULT f_opendir(DIR* dp, const TCHAR* path)`` -> FR_OK."""
        path = _read_cstr(qemu, qemu.get_arg(1))
        self._build_dir()
        print(f"[Storage] f_opendir({path!r}) -> FR_OK "
              f"({len(self._dir_entries)} entries)", flush=True)
        return True, FR_OK

    @bp_handler(["f_readdir"])
    def f_readdir(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``FRESULT f_readdir(DIR* dp, FILINFO* fno)`` -> FR_OK, next entry."""
        fno = qemu.get_arg(1)
        if self._dir_idx >= len(self._dir_entries):
            # End of directory: empty fname[0] is the loop terminator.
            qemu.write_memory(fno + FINFO_FNAME, 1, 0)
            print("[Storage] f_readdir() -> <end of dir>", flush=True)
            return True, FR_OK

        name, fsize, fattrib = self._dir_entries[self._dir_idx]
        self._dir_idx += 1

        qemu.write_memory(fno + FINFO_FSIZE, 4, fsize & 0xFFFFFFFF)
        qemu.write_memory(fno + FINFO_FATTRIB, 1, fattrib)
        # FatFs short-name buffer: uppercase 8.3, NUL-terminated, max 12 chars.
        nm = name.upper().encode("latin1")[: FINFO_FNAME_MAX - 1]
        for i, b in enumerate(nm):
            qemu.write_memory(fno + FINFO_FNAME + i, 1, b)
        qemu.write_memory(fno + FINFO_FNAME + len(nm), 1, 0)

        kind = "DIR " if (fattrib & AM_DIR) else "file"
        print(f"[Storage] f_readdir() -> {kind} {name!r} ({fsize} bytes)",
              flush=True)
        return True, FR_OK

    @bp_handler(["f_closedir"])
    def f_closedir(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``FRESULT f_closedir(DIR* dp)`` -> FR_OK."""
        print("[Storage] f_closedir() -> FR_OK", flush=True)
        return True, FR_OK

    # --- file read (cat / load_config) ----------------------------------
    def _lookup(self, path: str) -> bytes | None:
        """Resolve a guest path (possibly with leading slash) to file bytes."""
        name = path.lstrip("/").lstrip("0:").lstrip("/")
        if name in self.files:
            return self.files[name]
        # Case-insensitive / 8.3-uppercase fallback (the firmware may pass the
        # name back in the case the user typed, which may be uppercased).
        low = name.lower()
        for fn, content in self.files.items():
            if fn.lower() == low:
                return content
        return None

    @bp_handler(["f_open"])
    def f_open(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``FRESULT f_open(FIL* fp, const TCHAR* path, BYTE mode)``."""
        fp = qemu.get_arg(0)
        path = _read_cstr(qemu, qemu.get_arg(1))
        content = self._lookup(path)
        if content is None:
            print(f"[Storage] f_open({path!r}) -> FR_NO_FILE", flush=True)
            return True, FR_NO_FILE
        # Pre-split into lines for f_gets; keep raw for f_read.
        lines = content.splitlines(keepends=True)
        self._open[fp] = {"name": path, "data": content, "lines": lines,
                          "lpos": 0, "rpos": 0}
        print(f"[Storage] f_open({path!r}) -> FR_OK ({len(content)} bytes)",
              flush=True)
        return True, FR_OK

    @bp_handler(["f_gets"])
    def f_gets(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``TCHAR* f_gets(TCHAR* buff, int len, FIL* fp)`` -> buff or NULL."""
        buff = qemu.get_arg(0)
        maxlen = qemu.get_arg(1) & 0x7FFFFFFF
        fp = qemu.get_arg(2)
        st = self._open.get(fp)
        if st is None or st["lpos"] >= len(st["lines"]):
            print("[Storage] f_gets() -> EOF", flush=True)
            return True, 0
        line = st["lines"][st["lpos"]]
        st["lpos"] += 1
        # f_gets stores at most len-1 chars + NUL.
        out = line[: max(0, maxlen - 1)]
        for i, b in enumerate(out):
            qemu.write_memory(buff + i, 1, b)
        qemu.write_memory(buff + len(out), 1, 0)
        print(f"[Storage] f_gets({st['name']!r}) -> "
              f"{out.rstrip(b' ').rstrip(bytes([13, 10]))!r} "
              f"({len(out)} bytes)", flush=True)
        return True, buff

    @bp_handler(["f_read"])
    def f_read(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``FRESULT f_read(FIL* fp, void* buff, UINT btr, UINT* br)``."""
        fp = qemu.get_arg(0)
        buff = qemu.get_arg(1)
        btr = qemu.get_arg(2) & 0xFFFFFFFF
        br_ptr = qemu.get_arg(3)
        st = self._open.get(fp)
        if st is None:
            if br_ptr:
                qemu.write_memory(br_ptr, 4, 0)
            print("[Storage] f_read() on unknown handle -> FR_OK 0 bytes",
                  flush=True)
            return True, FR_OK
        chunk = st["data"][st["rpos"]: st["rpos"] + btr]
        st["rpos"] += len(chunk)
        if chunk:
            # Backend-agnostic bulk write. A plain write_memory(buff, N, bytes)
            # works on unicorn but the avatar2 backend treats N as the element
            # size (KeyError on its 1/2/4/8 struct-format map). write_memory_bytes
            # uses size=1, num_words=len, raw=True — correct on every backend.
            qemu.write_memory_bytes(buff, bytes(chunk))
        if br_ptr:
            qemu.write_memory(br_ptr, 4, len(chunk))
        print(f"[Storage] f_read({st['name']!r}) -> {len(chunk)} bytes",
              flush=True)
        return True, FR_OK

    @bp_handler(["f_close"])
    def f_close(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``FRESULT f_close(FIL* fp)`` -> FR_OK."""
        fp = qemu.get_arg(0)
        self._open.pop(fp, None)
        print("[Storage] f_close() -> FR_OK", flush=True)
        return True, FR_OK
