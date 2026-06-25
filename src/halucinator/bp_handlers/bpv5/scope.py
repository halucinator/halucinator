# Copyright 2026 Christopher Wright

"""Bus Pirate v5 (RP2040) — Scope (#14) display mode + BINLOOP (#13) binmode.

Two of the last menu modes of the full-device rehost, modeled here.

Scope (#14) — the oscilloscope DISPLAY mode
============================================
RE finding (capstone Thumb, flash base 0x10000000): "Scope" is NOT a `m`-menu
*bus* mode — it is **display-mode index 1** in the firmware's display-mode
descriptor table (stride 0x40, base 0x20001990; idx0=Default, idx1=Scope,
idx2=Disabled).  The interactive CLI selects it with the **`d` command**:
``ui_display_enable_args`` (0x10026b54) validates ``N<=3``, then uses ``N-1`` to
index the table — so **`d 2` enters Scope**.  It calls the entry's ``cleanup``
(+0xc), ``setup`` (+4 -> ``scope_setup`` @0x10040e68) and, on success,
``setup_exc`` (+8 -> ``scope_setup_exc`` @0x10040e9c), then
``printf("\r\n%s%s:%s %s", ..., name)`` which renders ``Display: Scope`` to the
console — our console-visible proof the mode was entered.

``scope_setup_exc`` (run for REAL here) allocs a 0xfb00-byte block via the real
``mem_alloc`` and records three working pointers (RE'd from its literal pool):

    0x20038c50  framebuffer base      (alloc + 0x0000;  0x9600 px region)
    0x20038c30  framebuffer end       (alloc + 0x9600)
    0x20038ad4  **sample buffer**     (alloc + 0xc880;  uint16 ADC samples)

The acquisition+render pipeline is a free-running loop that does NOT fire under
HALucinator (no live core1 / DMA / sample-clock IRQ):

    scope_periodic            polls the trigger GPIO (SIO 0xd0000000+4)
      -> scope_start.part.0   arms ADC (0x4004c000) + AMUX + a DMA channel
         -> [ADC->DMA fills the uint16 sample buffer @0x20038ad4]
            -> DMA-done IRQ (0x1003aee4) scans 0x40 samples -> min/max + trigger
               (results @0x2003dc44 trigger, 0x2003d7bc/0x2003d7c0 trace pos)
               -> scope_lcd_update (0x100420a0) plots the trace into the
                  framebuffer (raw pixels + the V/div & time/div labels
                  "0.5Vx"/"1mS"/...), then blits it to the ST7789.

Modeling level — HLE at the safe seams + a MODELED waveform the firmware's own
buffers carry:

* ``lcd_enable`` -> no-op (the raw SPI/SIO ST7789 power-on would fault headless;
  the existing LCD draw-capture model owns the panel).
* ``scope_setup_exc`` runs for REAL (observe-only) so the sample/framebuffer
  pointers are recorded by the firmware itself.
* ``scope_periodic`` -> we read back the firmware-recorded **sample buffer
  pointer** (0x20038ad4) and write a KNOWN modeled waveform into it (0x40
  uint16 samples), then log it.  Because the DMA never runs headless, this is
  the data the firmware's reducer/renderer would consume — so the scope trace
  reflects OUR level.  Default is a flat DC level (raw 0x800 == mid-scale ->
  ~3.3 V); a ``ramp``/``triangle`` waveform is selectable.

ADC raw->mV scale (same /2 input divider + 3300 mV ref as ``amux_sweep``):
    mV = (raw * 6600) >> 12        (raw 0x800 == 2048 -> 3300 mV == 3.3 V)

BINLOOP (#13) — binmode binary loopback  (INTRACTABLE for a console proof)
==========================================================================
binmode is the Bus Pirate "binary scripting" console protocol; the default
binmode protocol is the SUMP logic analyzer (table @0x100b0970 idx0; the SUMP
ID/metadata query emits "RP2040 NNMhz" via sump_rx/cdc_sump_task).  Landing it
as a console-visible loopback proved intractable under headless unicorn, for
two independently-blocking reasons found by RE + an empirical test:

* **Runs on core1.** binmode's service loop lives in ``core1_entry``
  (0x10000330), which is never launched headless — ``multicore_launch_core1``
  is SkipFunc'd, so core1 never runs.  ``binmode_service`` IS reachable once on
  core0 at init, but the real service loop is a core1 job.
* **I/O on USB CDC interface 1.** binmode reads/writes via ``tud_cdc_n_*(1,..)``
  — a SEPARATE USB serial channel from the terminal's interface 0 — so the
  binmode/SUMP identity is never delivered to the console the test terminal is
  wired to.
* **Big-buffer conflict.** Un-skipping ``binmode_service`` makes the real
  service run ``logicanalyzer_setup`` at boot, which claims the single-owner
  "big buffer"; Scope's ``scope_setup`` then fails ("the big buffer is already
  allocated", scope mode broken).  So binmode_service MUST stay SkipFunc'd for
  Scope to work.

``BinmodeModel`` below is the investigation artifact (an observe-only service
hook).  It is NOT wired in the config — landing binmode would need a core1
execution model plus a capture of USB CDC interface 1.  Scope is the delivered,
higher-value mode.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Tuple

from halucinator.bp_handlers.bp_handler import BPHandler, bp_handler

if TYPE_CHECKING:
    from halucinator.backends.hal_backend import HalBackend

# Firmware globals scope_setup_exc records (RE'd from its literal pool).
_SAMPLE_BUF_PTR = 0x20038AD4   # uint16[*] ADC sample buffer (alloc + 0xc880)
_FRAMEBUF_PTR = 0x20038C50     # framebuffer base
# Result globals the DMA-done IRQ computes from the sample buffer.
_TRIGGER_VAL = 0x2003DC44      # uint16 — the trace's locked trigger level
_SAMPLES_PER_FRAME = 0x40      # the IRQ scans 0x40 samples per trace column


class ScopeModel(BPHandler):
    """Modeled oscilloscope display mode for the Bus Pirate v5."""

    # ADC raw->mV: /2 divider, 3300 mV ref, 12-bit  => mV = raw*6600 >> 12.
    @staticmethod
    def _raw_to_mv(raw: int) -> int:
        return (raw * 6600) >> 12

    def __init__(
        self,
        level_raw: Optional[int] = None,
        waveform: str = "dc",
        samples: int = _SAMPLES_PER_FRAME,
    ) -> None:
        super().__init__()
        # Default modeled level: mid-scale (0x800 == 2048) -> ~3.3 V.
        self.level_raw = 0x800 if level_raw is None else int(level_raw)
        self.waveform = waveform
        self.samples = int(samples)
        self._entered = False
        self._injected = 0
        print(
            "[ScopeModel] modeled oscilloscope attached "
            f"(waveform={self.waveform}, level_raw={self.level_raw:#06x} "
            f"-> {self._raw_to_mv(self.level_raw)} mV)",
            flush=True,
        )

    # --- modeled waveform -----------------------------------------------
    def _sample(self, i: int) -> int:
        """The i-th modeled uint16 ADC sample (0..0xfff scale)."""
        if self.waveform == "ramp":
            return (self.level_raw + i * 8) & 0x0FFF
        if self.waveform == "triangle":
            half = self.samples // 2
            tri = i if i < half else (self.samples - i)
            return (self.level_raw + tri * 16) & 0x0FFF
        return self.level_raw & 0x0FFF  # flat DC

    # --- neuter the raw ST7789 power-on ---------------------------------
    @bp_handler(["lcd_enable"])
    def lcd_enable(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        print("[ScopeModel] lcd_enable (modeled no-op — panel power-on skipped)",
              flush=True)
        return True, 0

    # --- scope mode entry: observe-only over the real setup_exc ----------
    @bp_handler(["setup_exc"])
    def setup_exc(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        self._entered = True
        print("[ScopeModel] scope_setup_exc — entering Scope display mode "
              f"(modeled {self.waveform} waveform @ "
              f"{self._raw_to_mv(self.level_raw)} mV)", flush=True)
        return False, 0  # observe-only: let the real setup_exc alloc + record

    # --- per-loop: inject the modeled waveform into the real buffer ------
    @bp_handler(["periodic"])
    def periodic(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``scope_periodic()`` — called every core0 loop while Scope is active.

        Read back the firmware-recorded sample-buffer pointer and write our
        modeled waveform into it (the DMA that would normally fill it never
        runs headless), then publish the trigger level the reducer derives.
        Observe-only otherwise (let the real periodic run its trigger-poll).
        """
        if not self._entered:
            return False, 0
        try:
            buf = qemu.read_memory(_SAMPLE_BUF_PTR, 4, 1) & 0xFFFFFFFF
        except Exception:  # noqa: BLE001
            buf = 0
        if buf and self._injected < 3:
            for i in range(self.samples):
                qemu.write_memory(buf + i * 2, 2, self._sample(i) & 0xFFFF)
            # Publish the trigger level the firmware's reducer locks the trace
            # to, so the rendered trace reflects our modeled level.
            qemu.write_memory(_TRIGGER_VAL, 2, self.level_raw & 0xFFFF)
            self._injected += 1
            mv = self._raw_to_mv(self.level_raw)
            print(
                f"[ScopeModel] scope sample buffer @{buf:#010x} <- "
                f"modeled {self.waveform} waveform "
                f"({self.samples} samples, level raw={self.level_raw:#06x} "
                f"-> {mv} mV = {mv/1000:.2f} V); trigger@{_TRIGGER_VAL:#x} "
                f"= {self.level_raw:#06x}",
                flush=True,
            )
        return False, 0  # observe-only: real periodic still polls the trigger


class BinmodeModel(BPHandler):
    """Modeled BINLOOP / binmode binary console protocol entry.

    Un-skips ``binmode_service`` so the firmware actually runs binmode and its
    real output is observable on the console.
    """

    def __init__(self) -> None:
        super().__init__()
        self._entered = False
        print("[BinmodeModel] modeled binmode attached", flush=True)

    @bp_handler(["service"])
    def service(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        if not self._entered:
            self._entered = True
            print("[BinmodeModel] binmode_service — entering binmode "
                  "(observe-only; firmware drives the protocol)", flush=True)
        return False, 0  # observe-only: let the real binmode_service run
