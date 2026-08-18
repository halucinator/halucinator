"""A-profile ARM guests must keep their instruction set across a resume.

unicorn derives the Thumb flag from bit 0 of the address handed to *every*
``emu_start`` call. The backend used to OR in that bit only for ``_is_thumb``
(M-profile) targets, so an ``arch: arm`` guest executing Thumb -- i.e. any
ARMv4T/v5 image built ``-mthumb-interwork``, which is the normal shape for
classic-ARM embedded firmware -- was silently switched back to ARM decoding at
every ``irq_chunk`` boundary and every ``step()``.

The first test pins the unicorn behaviour these tests exist to compensate for;
the second pins the backend helper that compensates for it.
"""
import pytest

unicorn = pytest.importorskip("unicorn")
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UcError  # noqa: E402
from unicorn import arm_const as A  # noqa: E402

# thumb: movs r0,#1 ; movs r1,#2 ; b .
THUMB_CODE = bytes.fromhex("0120") + bytes.fromhex("0221") + bytes.fromhex("fee7")


def test_unicorn_reverts_to_arm_when_resumed_at_an_even_pc():
    """The upstream behaviour. If this ever starts passing without the OR,
    unicorn changed and the helper below can be revisited."""
    mu = Uc(UC_ARCH_ARM, UC_MODE_ARM)
    mu.mem_map(0x1000, 0x1000)
    mu.mem_write(0x1000, THUMB_CODE)
    mu.emu_start(0x1001, 0, count=1)                 # odd -> Thumb
    pc = mu.reg_read(A.UC_ARM_REG_PC)
    assert pc == 0x1002
    assert mu.reg_read(A.UC_ARM_REG_CPSR) & 0x20     # CPSR.T is set
    with pytest.raises(UcError):                     # even resume -> ARM -> derail
        mu.emu_start(pc, 0, count=1)
    # ... and the corrected resume runs the next Thumb instruction.
    mu.emu_start(pc | 1, 0, count=1)
    assert mu.reg_read(A.UC_ARM_REG_R1) == 2


def _backend_for(arch):
    from halucinator.backends.unicorn_backend import UnicornBackend
    b = UnicornBackend.__new__(UnicornBackend)
    b.arch_name = arch
    b._is_thumb = (arch == "cortex-m3")
    return b


def test_resume_addr_follows_cpsr_t_on_a_profile_arm():
    b = _backend_for("arm")

    class _FakeUc:
        def __init__(self, cpsr):
            self._cpsr = cpsr

        def reg_read(self, _regid):
            return self._cpsr

    b._uc = _FakeUc(0x1D3)                 # ARM state (T clear)
    assert b._resume_addr(0x102000) == 0x102000
    b._uc = _FakeUc(0x1F3)                 # Thumb state (T set)
    assert b._resume_addr(0x102000) == 0x102001


def test_resume_addr_always_sets_the_bit_on_m_profile():
    b = _backend_for("cortex-m3")
    b._uc = None                            # must not be consulted
    assert b._resume_addr(0x8000100) == 0x8000101
