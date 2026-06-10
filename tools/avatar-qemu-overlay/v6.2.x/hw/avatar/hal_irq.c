/*
 * halucinator-native IRQ injection QMP commands.
 *
 * One file holding all four hal-* commands. They are declared UNGUARDED in
 * the qapi schema (so the generated common qapi-types header never names a
 * TARGET_* macro — QEMU 11 poisons those in target-agnostic units) and the
 * arch-specific bodies are selected here with #ifdef, which is legal in
 * this per-target compilation unit. A command invoked on a target it
 * doesn't apply to returns a clear error rather than failing to link.
 *
 * No avatar2 dependency.
 */

#include "qemu/osdep.h"
#include "qemu/log.h"
/* LOG_AVATAR lives in qemu/log.h in the forks but in an overlay header when
 * applied to upstream QEMU; define defensively so this builds in both. */
#ifndef LOG_AVATAR
#define LOG_AVATAR (1 << 22)
#endif
#include "qemu/error-report.h"
#include "qapi/qapi-commands-avatar-target.h"
#include "qapi/error.h"
#include "exec/cpu-common.h"
#if __has_include("system/memory.h")
#include "system/memory.h"            /* QEMU 11+ */
#else
#include "exec/memory.h"             /* QEMU 10.x / 6.2 */
#endif
#if __has_include("system/address-spaces.h")
#include "system/address-spaces.h"   /* QEMU 11+ */
#else
#include "exec/address-spaces.h"     /* QEMU 10.x / 6.2 */
#endif
#if __has_include("hw/core/irq.h")
#include "hw/core/irq.h"            /* QEMU 11+ */
#else
#include "hw/irq.h"                 /* QEMU 10.x / 6.2 */
#endif
#if __has_include("hw/core/qdev.h")
#include "hw/core/qdev.h"           /* QEMU 11+ */
#else
#include "hw/qdev-core.h"           /* QEMU 10.x / 6.2 */
#endif

/* Target endianness: QEMU 7+ uses TARGET_BIG_ENDIAN (always defined, 0/1)
 * and poisons the old TARGET_WORDS_BIGENDIAN; QEMU 6.2 only defines
 * TARGET_WORDS_BIGENDIAN (on BE targets). The #elif is never evaluated
 * when TARGET_BIG_ENDIAN is defined, so the poisoned name is never named
 * on a 7+ build. */
#if defined(TARGET_BIG_ENDIAN)
#  define HAL_TARGET_BE TARGET_BIG_ENDIAN
#elif defined(TARGET_WORDS_BIGENDIAN)
#  define HAL_TARGET_BE 1
#else
#  define HAL_TARGET_BE 0
#endif

#if defined(TARGET_ARM) || defined(TARGET_AARCH64)
#include "qom/object.h"
#if __has_include("hw/core/sysbus.h")
#include "hw/core/sysbus.h"          /* QEMU 11+ */
#else
#include "hw/sysbus.h"               /* QEMU 10.x / 6.2 */
#endif
#endif

#if defined(TARGET_MIPS)
#include "target/mips/cpu.h"
#endif

#if defined(TARGET_PPC) || defined(TARGET_PPC64)
#include "qemu/main-loop.h"
#include "target/ppc/cpu.h"
#include "hw/ppc/ppc.h"
#endif

/* ---- hal-shadow-irq: arch-agnostic shadow-write ---- */
void qmp_hal_shadow_irq(int64_t number_addr, int64_t fired_addr,
                        int64_t irq_num, Error **errp)
{
#if HAL_TARGET_BE
    address_space_stl_be(&address_space_memory, (hwaddr)number_addr,
                         (uint32_t)irq_num, MEMTXATTRS_UNSPECIFIED, NULL);
    address_space_stl_be(&address_space_memory, (hwaddr)fired_addr,
                         1, MEMTXATTRS_UNSPECIFIED, NULL);
#else
    address_space_stl_le(&address_space_memory, (hwaddr)number_addr,
                         (uint32_t)irq_num, MEMTXATTRS_UNSPECIFIED, NULL);
    address_space_stl_le(&address_space_memory, (hwaddr)fired_addr,
                         1, MEMTXATTRS_UNSPECIFIED, NULL);
#endif
    qemu_log_mask(LOG_AVATAR, "hal-shadow-irq: num=%" PRId64 " @0x%" PRIx64
                  " fired=1 @0x%" PRIx64 "\n", irq_num, number_addr, fired_addr);
}

/* ---- hal-arm-inject-irq: pulse a GIC SPI line ---- */
void qmp_hal_arm_inject_irq(int64_t num_cpu, int64_t num_irq, Error **errp)
{
#if defined(TARGET_ARM) || defined(TARGET_AARCH64)
    qemu_log_mask(LOG_AVATAR, "hal-arm-inject-irq: IRQ %" PRId64 " cpu %"
                  PRId64 "\n", num_irq, num_cpu);
    Object *gic = object_resolve_path_component(qdev_get_machine(), "gic");
    if (gic == NULL) {
        error_setg(errp, "hal-arm-inject-irq: no peripheral named 'gic' "
                   "(declare one with qemu_name: arm_gic)");
        return;
    }
    DeviceState *gicdev = (DeviceState *)object_dynamic_cast(gic, TYPE_DEVICE);
    if (gicdev == NULL) {
        error_setg(errp, "hal-arm-inject-irq: 'gic' is not a DeviceState");
        return;
    }
    if (num_irq < 0 || num_irq > 1019) {
        error_setg(errp, "hal-arm-inject-irq: num-irq must be 0..1019");
        return;
    }
    qemu_irq line = qdev_get_gpio_in(gicdev, num_irq);
    if (line == NULL) {
        error_setg(errp, "hal-arm-inject-irq: GIC has no GPIO input %"
                   PRId64, num_irq);
        return;
    }
    qemu_irq_pulse(line);
#else
    error_setg(errp, "hal-arm-inject-irq: not an ARM target");
#endif
}

/* ---- hal-mips-inject-irq: assert a Cause.IP pin ---- */
void qmp_hal_mips_inject_irq(int64_t num_cpu, int64_t num_irq, Error **errp)
{
#if defined(TARGET_MIPS)
    qemu_log_mask(LOG_AVATAR, "hal-mips-inject-irq: IRQ %" PRId64 " cpu %"
                  PRId64 "\n", num_irq, num_cpu);
    CPUState *cs = qemu_get_cpu(num_cpu);
    if (cs == NULL) {
        error_setg(errp, "hal-mips-inject-irq: cpu %" PRId64 " not found",
                   num_cpu);
        return;
    }
    if (num_irq < 0 || num_irq > 7) {
        error_setg(errp, "hal-mips-inject-irq: num-irq must be 0..7");
        return;
    }
    qemu_irq line = qdev_get_gpio_in(DEVICE(cs), num_irq);
    if (line == NULL) {
        error_setg(errp, "hal-mips-inject-irq: cpu has no GPIO input %"
                   PRId64, num_irq);
        return;
    }
    qemu_irq_pulse(line);
#else
    error_setg(errp, "hal-mips-inject-irq: not a MIPS target");
#endif
}

/* ---- hal-ppc-inject-irq: raise the external interrupt ---- */
#if defined(TARGET_PPC) || defined(TARGET_PPC64)
typedef struct { PowerPCCPU *cpu; QEMUBH *bh; } PpcDeassertCtx;
static void hal_ppc_deassert_bh(void *opaque)
{
    PpcDeassertCtx *ctx = opaque;
    ppc_set_irq(ctx->cpu, PPC_INTERRUPT_EXT, 0);
    qemu_bh_delete(ctx->bh);
    g_free(ctx);
}
#endif

void qmp_hal_ppc_inject_irq(int64_t num_cpu, int64_t num_irq, Error **errp)
{
#if defined(TARGET_PPC) || defined(TARGET_PPC64)
    qemu_log_mask(LOG_AVATAR, "hal-ppc-inject-irq: IRQ %" PRId64 " cpu %"
                  PRId64 "\n", num_irq, num_cpu);
    CPUState *cs = qemu_get_cpu(num_cpu);
    if (cs == NULL) {
        error_setg(errp, "hal-ppc-inject-irq: cpu %" PRId64 " not found",
                   num_cpu);
        return;
    }
    PowerPCCPU *cpu = POWERPC_CPU(cs);
    ppc_set_irq(cpu, PPC_INTERRUPT_EXT, 1);
    PpcDeassertCtx *ctx = g_new0(PpcDeassertCtx, 1);
    ctx->cpu = cpu;
    ctx->bh = qemu_bh_new(hal_ppc_deassert_bh, ctx);
    qemu_bh_schedule(ctx->bh);
#else
    error_setg(errp, "hal-ppc-inject-irq: not a PowerPC target");
#endif
}
