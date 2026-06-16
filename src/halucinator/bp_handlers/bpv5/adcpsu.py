# Copyright 2026 Christopher Wright

"""Bus Pirate v5 (RP2040) — modeled analog subsystem: ADC + AMUX + PSU.

This is the always-on analog companion to the per-mode fan-out targets
(``SpiFlashTarget``, ``I2cEepromTarget``, ``DioPinTarget``, ...).  It makes the
firmware's *voltage* path real instead of SkipFunc-spoofed-to-zero, so the live
CLI ``v`` (measure pin voltages) and ``W`` (enable the programmable supply)
commands report **modeled** voltages, and the boot/preflight
"No voltage detected on VOUT/VREF" warning disappears because a powered, sane
board is presented to the firmware.

Modeling level — HLE at ``amux_sweep`` (the analog acquisition seam)
====================================================================
Every voltage the firmware ever prints flows through one global refresh:

    amux_sweep()  @ 0x100065b0

which (a) busy-polls the RP2040 ADC FIFO (``0x4004c000`` + 4) to fill a raw
uint16 array, then (b) converts each raw count into **millivolts** in a global
results struct.  The interactive ``v``/``W`` commands, ``psu_measure``, and the
``ui_help_sanity_check`` preflight all just *read* that struct afterwards.

The real ``amux_sweep`` busy-polls ADC MMIO that isn't modeled (it lives in the
``logger`` GenericPeripheral catch-all), which is exactly why the stock config
SkipFunc'd it (``skip_amux`` on ``amux_sweep``) — leaving the struct all-zero,
hence "No voltage detected".  We **replace that skip with an HLE** that writes
*modeled* millivolt values straight into the struct.  No ADC MMIO state machine
to emulate, backend-agnostic (unicorn + avatar2), and the firmware's own
rendering/conversion code runs unchanged on top of our data.

Struct map (RE'd — Thumb, flash base 0x10000000; from capstone disasm of
``bus_pirate5_rev10.bin`` + the literal pools)
----------------------------------------------------------------------
* Raw ADC array  @ ``0x200394f4`` — uint16[13]; ``amux_sweep`` fills idx 0..11
  from the ADC FIFO, idx 12 (off 0x18) is the averaged VREF/current channel.
* Results struct  @ ``0x20039510`` — per-channel **millivolts** (u32 each):

      +0x00 IO0   +0x04 IO1   +0x08 IO2   +0x0c IO3
      +0x10 IO4   +0x14 IO5   +0x18 IO6   +0x1c IO7
      +0x20 aux   +0x24 aux   +0x28 PSU current-rail   +0x2c **VOUT**
      +0x30 **VREF**

  Decisive reads proving +0x2c == VOUT (mV):
    - ui_help_sanity_check     @0x1002789c: ldr [r3,#0x2c]; cmp >0x315 (789)
    - ui_help_check_vout_vref  @0x10027828: ldr [r3,#0x2c]; cmp >0x315
    - ui_info_print_pin_voltage@0x10026d20: ldr [r3,#0x2c]; /1000 -> volts
    - psu_measure              @0x10005386: ldr [r3,#0x2c]
* EMA accumulator @ ``0x200394c0`` — mirrors the results struct field-for-field
  holding ``mV * 64`` (``amux_sweep`` runs an exponential moving average:
  ``acc -= (acc+0x20)>>6; acc += mV``).  We seed it with ``mV*64`` so a second
  sweep doesn't drift the displayed value away from our model.

Raw<->mV conversion the firmware uses (for reference; we write mV directly):
    IO/VOUT channels:  mV = (raw * 6600) >> 12      (== raw * 6600 / 4096;
                       /2 input divider, 3300 mV ref => ~6.6 V full scale)
    VREF channel:      mV = (raw * 3300) >> 12      (no /2 divider)

Modeled rails (override via ``registration_args``)
--------------------------------------------------
* **VOUT = 3300 mV** and **VREF = 3300 mV** — a powered 3.3 V board, so the
  sanity check (VOUT > 789 mV) passes naturally and the warning is gone.
* Each **BIO pin a distinct, recognizable voltage** so a captured ``v`` reading
  is obviously real modeled data (a ramp by default: IO0=0.40 V, IO1=0.80 V,
  ... IO7=3.20 V — 400 mV * (pin+1)).
* The PSU set-point: when the firmware enables the supply (``W <volts>``) via
  ``psu_enable``, we capture the programmed set-point (``r0`` is the float
  target voltage) and make VOUT read it back, so ``W 3.3`` reports ~3.3 V.

Everything is logged (``[AdcPsuModel] ...``) so the run captures the modeled
analog state for verification.

ui_help_sanity_check — partial natural pass (the bonus finding)
---------------------------------------------------------------
The other modes work around the spoofed-off analog rails by forcing
``ui_help_sanity_check`` -> 1 (the I2C ``ReturnConstant`` intercept).  With this
model the **VOUT/VREF voltage half passes naturally**: the check calls
``amux_sweep`` (our HLE), reads ``[0x20039510+0x2c]`` = 3300 mV and compares
``> 0x315`` (789) — true, so "No voltage detected on VOUT/VREF" is gone WITHOUT
the force.  BUT the check's **pull-up half still fails**: it then calls
``bio_get(pin)`` on the I2C pins and expects them digitally HIGH (pull-ups), and
with the analog-only model those pins read 0 -> "Pull-up not detected on IO
pin".  So the I2C ``ui_help_sanity_check`` force-return is STILL required (kept)
until a pull-up/``bio_get`` model drives the I2C pins high; this model retires
only the voltage half of that synthesis.
"""
from __future__ import annotations

import json
import os
import struct
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from halucinator.bp_handlers.bp_handler import BPHandler, bp_handler

if TYPE_CHECKING:
    from halucinator.backends.hal_backend import HalBackend

# --- Global RAM locations (RE'd from the firmware) -----------------------
_RAW_ARR = 0x200394F4   # uint16[13] raw ADC counts
_RESULTS = 0x20039510   # u32[...] per-channel millivolts
_EMA_ACC = 0x200394C0   # u32[...] EMA accumulator == mV * 64

# Per-channel ASCII voltage-string cache the `v` command actually renders.
# monitor() (the periodic refresher) formats this from the mV results struct,
# but it only runs once at boot under HALucinator (its periodic driver — a
# timer/core1 — isn't live), so the `v` command reads a stale "0.0" cache. We
# populate it directly. Layout (RE'd by live dump): 9 NUL-terminated "X.X"
# slots of 4 bytes each, in DISPLAY order:  [Vout, IO0, IO1, ..., IO7].
#   monitor_get_voltage_ptr(ch) -> 0x2003dc18 + ch*4   (ch 0=Vout, 1..8=IO0..7)
_VCACHE = 0x2003DC18    # char[9][4]: "X.X\0" per channel
_VCACHE_SLOT = 4
_VCACHE_N = 9
# "valid/changed" bitmask words value_voltage's validity check ORs together.
_VVALID0 = 0x20037460
_VVALID8 = 0x20037468

# ui_process_commands (the CLI dispatcher @0x100255e0) gates power-related
# commands (W / power, P / pullups, G / pwm, ...) behind a runtime "power
# subsystem available" byte at 0x2003d6bc + 0x55 == 0x2003d711.  It is set by
# the PSU bring-up we SkipFunc (psu_init/psucmd_init), so under emulation it
# stays 0 and `W` silently fails to dispatch ("command needs ...").  We set it
# so `W` (and the other power commands) dispatch and reach our PSU model.
_CMD_POWER_GATE = 0x2003D6BC + 0x55  # 0x2003d711

# Results-struct byte offsets (millivolt u32 fields), indexed by DISPLAY pin
# (column IOn).  The firmware's `v`-table per-pin pointer table (@0x100adfe8)
# maps the displayed columns to the struct in REVERSE: column IO0 reads struct
# +0x1c, IO1 -> +0x18, ... IO7 -> +0x00.  So _OFF_IO[n] (== the offset whose
# value shows under the "IOn" column) is 0x1c - n*4.  (Verified live with a
# distinct per-pin ramp.)
_OFF_IO = [0x1C - n * 4 for n in range(8)]  # IO0..IO7 -> +0x1c..+0x00
_OFF_AUX = [0x20, 0x24]
_OFF_PSU_CURRENT = 0x28
_OFF_VOUT = 0x2C
_OFF_VREF = 0x30

# Raw-array indices that feed psu_measure's derived readings.
_RAW_IDX_CURRENT = 0x18  # idx 12 -> *122 == current sense in psu_measure
_RAW_IDX_FUSE = 0x12     # idx 9  -> compared > 300 == "vreg present" flag

# Firmware raw->mV conversion (for reference; we publish mV directly):
#   IO/VOUT channels:  mV = (raw * 6600) >> 12   (/2 divider, 3300 mV ref)
#   VREF channel:      mV = (raw * 3300) >> 12   (no divider)


class AdcPsuModel(BPHandler):
    """Modeled ADC/AMUX/PSU analog subsystem for the Bus Pirate v5.

    Replaces the ``amux_sweep`` SkipFunc spoof with an HLE that publishes
    modeled millivolt rails into the firmware's global results struct, so the
    firmware's own voltage rendering reports real values.
    """

    DEFAULT_VOUT_MV = 3300
    DEFAULT_VREF_MV = 3300
    # Distinct per-pin ramp so a captured `v` reading is obviously modeled:
    # IO(n) = 400 mV * (n+1)  ->  0.40, 0.80, 1.20, ... 3.20 V.
    DEFAULT_PIN_STEP_MV = 400
    # A plausible modeled current-sense rail (mV) for the PSU readback path.
    DEFAULT_PSU_CURRENT_MV = 50

    def __init__(
        self,
        vout_mv: Optional[int] = None,
        vref_mv: Optional[int] = None,
        pin_mv: Optional[List[int]] = None,
        pin_step_mv: Optional[int] = None,
        psu_current_mv: Optional[int] = None,
    ) -> None:
        super().__init__()
        self.vout_mv = self.DEFAULT_VOUT_MV if vout_mv is None else int(vout_mv)
        self.vref_mv = self.DEFAULT_VREF_MV if vref_mv is None else int(vref_mv)
        self.psu_current_mv = (
            self.DEFAULT_PSU_CURRENT_MV if psu_current_mv is None
            else int(psu_current_mv)
        )
        step = self.DEFAULT_PIN_STEP_MV if pin_step_mv is None else int(pin_step_mv)
        if pin_mv is not None:
            self.pin_mv = [int(v) for v in pin_mv][:8]
            self.pin_mv += [0] * (8 - len(self.pin_mv))
        else:
            self.pin_mv = [step * (n + 1) for n in range(8)]
        # PSU enable state: None until `W` programs a set-point.
        self.psu_enabled = False
        self.psu_setpoint_mv: Optional[int] = None
        # Config baselines — restored by _apply_overrides when the live-override
        # file is absent (so the panel's "reset to config" actually reverts).
        self._cfg_vout_mv = self.vout_mv
        self._cfg_vref_mv = self.vref_mv
        self._cfg_pin_mv = list(self.pin_mv)
        print(
            "[AdcPsuModel] modeled analog subsystem attached "
            f"(VOUT={self.vout_mv} mV, VREF={self.vref_mv} mV, "
            f"IO0..IO7={self.pin_mv} mV)",
            flush=True,
        )

    # --- helpers --------------------------------------------------------
    def _eff_vout_mv(self) -> int:
        """VOUT the board reads back: the PSU set-point if the supply is on,
        else the default rail (models VOUT pin externally powered)."""
        if self.psu_enabled and self.psu_setpoint_mv is not None:
            return self.psu_setpoint_mv
        return self.vout_mv

    def _w32(self, qemu: "HalBackend", addr: int, val: int) -> None:
        qemu.write_memory(addr, 4, val & 0xFFFFFFFF)

    def _w16(self, qemu: "HalBackend", addr: int, val: int) -> None:
        qemu.write_memory(addr, 2, val & 0xFFFF)

    def _publish(self, qemu: "HalBackend") -> Dict[str, int]:
        """Write the modeled millivolt rails into the firmware's global
        results struct (+ the EMA accumulator + the raw array). Returns the
        published field map for logging."""
        vout = self._eff_vout_mv()
        published: Dict[str, int] = {}

        # Per-pin IO voltages (struct +0x1c..+0x00, indexed by display column).
        # Also mirror into the EMA accumulator (mV*64) so a stray real sweep or
        # monitor pass wouldn't drift the value back.
        for n, off in enumerate(_OFF_IO):
            mv = self.pin_mv[n]
            self._w32(qemu, _RESULTS + off, mv)
            self._w32(qemu, _EMA_ACC + off, mv * 64)
            published[f"IO{n}"] = mv

        # Aux rails — mirror VOUT so nothing reads a stale zero.
        for off in _OFF_AUX:
            self._w32(qemu, _RESULTS + off, vout)
            self._w32(qemu, _EMA_ACC + off, vout * 64)

        # PSU "Vreg"/setpoint-feedback field (struct +0x28) — one of the values
        # psu_measure returns for the `W` summary. Track VOUT so "Vreg output"
        # reads back the modeled rail rather than 0.
        self._w32(qemu, _RESULTS + _OFF_PSU_CURRENT, vout)
        self._w32(qemu, _EMA_ACC + _OFF_PSU_CURRENT, vout * 64)

        # VOUT.
        self._w32(qemu, _RESULTS + _OFF_VOUT, vout)
        self._w32(qemu, _EMA_ACC + _OFF_VOUT, vout * 64)
        published["VOUT"] = vout

        # VREF.
        self._w32(qemu, _RESULTS + _OFF_VREF, self.vref_mv)
        self._w32(qemu, _EMA_ACC + _OFF_VREF, self.vref_mv * 64)
        published["VREF"] = self.vref_mv

        # Raw-array channels psu_measure derives from:
        #  - idx12 (off 0x18): psu_measure scales this by *122; it surfaces as a
        #    current-ish reading in the `W` summary. Seed a small plausible
        #    non-zero current (~10 mA: raw*122 ~= 10 mA worth).
        self._w16(qemu, _RAW_ARR + _RAW_IDX_CURRENT, 82)
        #  - idx9 (off 0x12) compared > 300 == "vreg present"; set it present
        #    whenever the supply is enabled (else 0 == not present).
        self._w16(qemu, _RAW_ARR + _RAW_IDX_FUSE, 400 if self.psu_enabled else 0)

        # Populate the ASCII voltage cache the `v` command renders (monitor's
        # job, but monitor doesn't run on the `v` path under emulation).
        self._publish_vcache(qemu, vout, published)

        # Open the CLI dispatcher's power-command gate so `W` (and the other
        # power commands) dispatch — normally set by the SkipFunc'd PSU init.
        qemu.write_memory(_CMD_POWER_GATE, 1, 1)

        return published

    @staticmethod
    def _fmt_volts(mv: int) -> bytes:
        """Format millivolts as the firmware's "X.X" cache string (volts +
        one decimal digit, matching monitor's divmod(mv,1000)/divmod(rem,100)).
        4-byte NUL-terminated slot."""
        mv = max(0, int(mv))
        volts = mv // 1000
        dec_tenths = (mv % 1000) // 100
        s = f"{volts}.{dec_tenths}".encode("ascii")[:3]
        return s.ljust(4, b"\x00")

    def _publish_vcache(self, qemu: "HalBackend", vout: int,
                        published: Dict[str, int]) -> None:
        """Write the 9-slot ASCII voltage cache (Vout, IO0..IO7) + validity."""
        slots = [vout] + [self.pin_mv[n] for n in range(8)]
        for ch, mv in enumerate(slots):
            qemu.write_memory(_VCACHE + ch * _VCACHE_SLOT, 1, self._fmt_volts(mv),
                              raw=True)
        # Mark all channels valid/changed so value_voltage's validity gate
        # passes and renders the (now non-zero) strings.
        self._w32(qemu, _VVALID0, 0xFFFFFFFF)
        self._w32(qemu, _VVALID8, 0xFFFFFFFF)

    def _apply_overrides(self) -> None:
        """Re-read live IO/rail overrides from a JSON file each sweep, so the
        web panel (bpv5_panel) can adjust the modeled pin voltages at runtime
        without a restart. File is optional; absent/malformed -> keep config
        values. Format: {"vout_mv": int, "vref_mv": int, "pin_mv": [8 ints]}."""
        path = os.environ.get("BPV5_ADC_FILE", "/tmp/bpv5_adc.json")
        try:
            with open(path) as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            d = {}   # no/invalid override file -> fall back to config baselines
        self.vout_mv = (int(d["vout_mv"])
                        if isinstance(d.get("vout_mv"), (int, float))
                        else self._cfg_vout_mv)
        self.vref_mv = (int(d["vref_mv"])
                        if isinstance(d.get("vref_mv"), (int, float))
                        else self._cfg_vref_mv)
        pm = d.get("pin_mv")
        if isinstance(pm, list) and pm:
            vals = [max(0, min(3300, int(v))) for v in pm][:8]
            vals += [0] * (8 - len(vals))
            self.pin_mv = vals
        else:
            self.pin_mv = list(self._cfg_pin_mv)   # restore config IO levels

    # --- ADC/AMUX acquisition seam: amux_sweep --------------------------
    @bp_handler(["sweep"])
    def sweep(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``amux_sweep()`` — refresh all channels.

        HLE: publish the modeled millivolt rails into the global results
        struct and return (skipping the real ADC busy-poll). The firmware's
        ``v``/``W``/sanity-check read this struct afterwards.
        """
        self._apply_overrides()
        published = self._publish(qemu)
        pins = " ".join(f"IO{n}={published[f'IO{n}']}" for n in range(8))
        print(
            f"[AdcPsuModel] amux_sweep -> VOUT={published['VOUT']} mV "
            f"VREF={published['VREF']} mV; {pins} mV "
            f"(PSU {'ON @ %d mV' % self.psu_setpoint_mv if self.psu_enabled else 'off'})",
            flush=True,
        )
        return True, 0

    # --- RP2040 SIO hardware-divider model ------------------------------
    # The firmware does integer division (incl. mV->volts for the `v` table)
    # via the RP2040 SIO hardware divider at 0xd0000000+0x60.. — write
    # dividend/divisor, read quotient/remainder.  Under emulation that window
    # is plain RAM (sio_ram), so the divider never computes: reads return 0 and
    # EVERY divide yields 0 (which is why the modeled voltages rendered as
    # "0.0V" even though the mV struct was correct).  We HLE the divmod helper
    # so division works.  ABI (RE'd @0x10048bac): divmod_u32u32(a, b) ->
    # quotient in r0, remainder in r1 (b==0 yields the RP2040 div-by-0 result).
    @bp_handler(["divmod_u32"])
    def divmod_u32(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        a = qemu.get_arg(0) & 0xFFFFFFFF
        b = qemu.get_arg(1) & 0xFFFFFFFF
        if b == 0:
            # RP2040 divider div-by-0: quotient = ~0 sign-style, remainder = a.
            q, r = 0xFFFFFFFF, a
        else:
            q, r = divmod(a, b)
        qemu.write_register("r1", r & 0xFFFFFFFF)
        return True, q & 0xFFFFFFFF

    @bp_handler(["divmod_s32"])
    def divmod_s32(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        def s32(v: int) -> int:
            v &= 0xFFFFFFFF
            return v - 0x100000000 if v & 0x80000000 else v
        a = s32(qemu.get_arg(0))
        b = s32(qemu.get_arg(1))
        if b == 0:
            q, r = 0xFFFFFFFF, a & 0xFFFFFFFF
        else:
            # C truncated division (toward zero), matching the HW divider.
            q = int(a / b) if (a < 0) ^ (b < 0) else a // b
            q = abs(a) // abs(b)
            if (a < 0) ^ (b < 0):
                q = -q
            r = a - q * b
        qemu.write_register("r1", r & 0xFFFFFFFF)
        return True, q & 0xFFFFFFFF

    # --- PSU set-point capture: psu_enable ------------------------------
    @bp_handler(["psu_enable"])
    def psu_enable(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``psu_enable(float volts, float current, ...) -> status``.

        Capture the programmed set-point so VOUT reads it back, then let the
        firmware continue (return 0 == success). ``r0`` holds the target
        voltage as an IEEE-754 single (the firmware computes the DAC code from
        it via ``__aeabi_fmul``/``float2uint``).
        """
        raw = qemu.get_arg(0) & 0xFFFFFFFF
        volts = struct.unpack("<f", struct.pack("<I", raw))[0]
        self.psu_setpoint_mv = max(0, int(round(volts * 1000.0)))
        self.psu_enabled = True
        # Refresh the struct immediately so any read before the next sweep
        # already sees the new set-point.
        self._publish(qemu)
        print(
            f"[AdcPsuModel] psu_enable(set={volts:.3f} V "
            f"-> {self.psu_setpoint_mv} mV) -> ON",
            flush=True,
        )
        return True, 0  # PSU_OK

    # --- `v`-command refresh seam: monitor_get_voltage_ptr --------------
    @bp_handler(["get_voltage_ptr"])
    def get_voltage_ptr(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``monitor_get_voltage_ptr(ch, &out_ptr) -> valid``.

        Called by ``value_voltage`` (the ``v`` command's per-channel renderer)
        right before it reads each channel's ASCII voltage string from the
        cache.  The cache is normally refreshed by the periodic ``monitor()``,
        which doesn't run on the ``v`` path under emulation — so we refresh it
        here just-in-time (publish the modeled mV struct + ASCII cache), then
        run the real getter (observe-only) so it returns the correct pointer
        and validity over our fresh data.
        """
        self._publish(qemu)
        return False, 0  # observe-only: real getter returns ptr+validity

    @bp_handler(["psu_disable"])
    def psu_disable(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``psu_disable()`` — turn the modeled supply off."""
        self.psu_enabled = False
        self.psu_setpoint_mv = None
        self._publish(qemu)
        print("[AdcPsuModel] psu_disable -> OFF", flush=True)
        return True, 0
