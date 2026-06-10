/*
 * ARMv7-A bare-metal IRQ test firmware for the multi_arch_irq corpus.
 *
 * Boots in ARM mode, sets up SVC + IRQ stacks, programs the GICv2
 * distributor + CPU interface, prints "READY" via the bp_handler-
 * intercepted uart_write(), and polls a flag set by the IRQ
 * handler. When the IRQ fires, the handler ACKs at GICC_IAR, marks
 * irq_fired, EOIs at GICC_EOIR, and main() prints "IRQ N FIRED".
 *
 * Default GIC base addresses (overrideable via macros):
 *   GICD_BASE = 0x08000000  (Distributor)
 *   GICC_BASE = 0x08010000  (CPU Interface)
 *
 * These are chosen so they DON'T overlap with the QEMU `virt`
 * machine's GIC (which lives at 0x08000000+0x10000 + 0x08010000) —
 * keep the layout consistent so existing toolchain assumptions hold.
 */
#include <stdint.h>

#ifndef GICD_BASE
#define GICD_BASE 0x08000000u
#endif
#ifndef GICC_BASE
#define GICC_BASE 0x08010000u
#endif

#define TEST_IRQ_NUM 33u   /* SPI 1 (32 + 1). SPI numbering matches
                            * the GICD ISPENDR1 bit-1 the Python
                            * GicController writes. */

/* GICD register offsets */
#define GICD_CTLR     0x000u
#define GICD_ISENABLER(n) (0x100u + (n) * 4u)
#define GICD_ICENABLER(n) (0x180u + (n) * 4u)
#define GICD_ISPENDR(n)   (0x200u + (n) * 4u)
#define GICD_ICPENDR(n)   (0x280u + (n) * 4u)
#define GICD_IPRIORITYR(n) (0x400u + (n) * 4u)
#define GICD_ITARGETSR(n) (0x800u + (n) * 4u)

/* GICC register offsets */
#define GICC_CTLR     0x000u
#define GICC_PMR      0x004u
#define GICC_BPR      0x008u
#define GICC_IAR      0x00Cu
#define GICC_EOIR     0x010u

#define MMIO32(addr) (*(volatile uint32_t *)(uintptr_t)(addr))

extern void uart_init(uint32_t uart_id);
extern void uart_write(uint32_t uart_id, const char *buf, uint32_t len);

volatile uint32_t _uart_sink;
__attribute__((used, noinline)) void uart_init(uint32_t uart_id) {
    _uart_sink = uart_id;
}
__attribute__((used, noinline)) void uart_write(uint32_t uart_id,
                                                const char *buf,
                                                uint32_t len) {
    _uart_sink = (uint32_t)(uintptr_t)buf ^ uart_id ^ len;
}

static volatile uint32_t irq_fired = 0;
static volatile uint32_t irq_number = 0;

static void gic_init(void) {
    /* Disable distributor + CPU interface while configuring. */
    MMIO32(GICD_BASE + GICD_CTLR) = 0;
    MMIO32(GICC_BASE + GICC_CTLR) = 0;

    /* Set priority of TEST_IRQ_NUM to 0xA0 (mid-range, above mask). */
    {
        uint32_t off = GICD_IPRIORITYR(TEST_IRQ_NUM / 4);
        uint32_t shift = (TEST_IRQ_NUM % 4) * 8;
        uint32_t v = MMIO32(GICD_BASE + off);
        v = (v & ~(0xFFu << shift)) | (0xA0u << shift);
        MMIO32(GICD_BASE + off) = v;
    }

    /* Route TEST_IRQ_NUM to CPU 0 (target byte = 0x01). */
    {
        uint32_t off = GICD_ITARGETSR(TEST_IRQ_NUM / 4);
        uint32_t shift = (TEST_IRQ_NUM % 4) * 8;
        uint32_t v = MMIO32(GICD_BASE + off);
        v = (v & ~(0xFFu << shift)) | (0x01u << shift);
        MMIO32(GICD_BASE + off) = v;
    }

    /* Enable TEST_IRQ_NUM at the distributor. */
    MMIO32(GICD_BASE + GICD_ISENABLER(TEST_IRQ_NUM / 32))
        = 1u << (TEST_IRQ_NUM % 32);

    /* CPU interface: priority mask high enough to allow our IRQ. */
    MMIO32(GICC_BASE + GICC_PMR) = 0xFF;
    MMIO32(GICC_BASE + GICC_BPR) = 0;

    /* Enable distributor (group 0 + 1) and CPU interface. */
    MMIO32(GICD_BASE + GICD_CTLR) = 1;
    MMIO32(GICC_BASE + GICC_CTLR) = 1;
}

/* IRQ vector: ack, mark fired, EOI. Called from the IRQ exception
 * vector in startup.s after lr_irq fixup. */
__attribute__((used)) void IRQ_Handler(void) {
    uint32_t iar = MMIO32(GICC_BASE + GICC_IAR);
    uint32_t intid = iar & 0x3FFu;
    if (intid != 0x3FFu) {  /* 0x3FF = spurious */
        irq_number = intid;
        irq_fired = 1;
    }
    MMIO32(GICC_BASE + GICC_EOIR) = iar;
}

/* Default handler for the other vectors (undef/swi/abort/...). Spin
 * with a sentinel so a stray exception is visible in logs. */
__attribute__((used)) void Default_Handler(void) {
    static const char msg[] = "ARM_FAULT\n";
    uart_write(0x50000000, msg, sizeof(msg) - 1);
    while (1) { }
}

void main(void) {
    uart_init(0x50000000);
    gic_init();

    static const char ready[] = "READY\n";
    uart_write(0x50000000, ready, sizeof(ready) - 1);

    /* Enable IRQs in CPSR (clear I bit). The FIQ bit (F) stays
     * masked since we only test IRQ delivery. */
    __asm__ volatile ("cpsie i" ::: "memory");

    while (!irq_fired) {
        __asm__ volatile ("nop");
    }

    /* Format "IRQ NN FIRED\n" without using uidivmod (no libgcc with
     * -nostdlib). Hand-decode by repeated subtraction of powers of 10. */
    char out[32];
    const char *p = "IRQ ";
    int o = 0;
    while (*p) out[o++] = *p++;
    uint32_t n = irq_number;
    if (n == 0) {
        out[o++] = '0';
    } else {
        char digits[4];
        int d = 0;
        while (n > 0 && d < 4) {
            uint32_t q = 0, t = n;
            while (t >= 10) { t -= 10; q++; }
            digits[d++] = '0' + (n - q * 10);
            n = q;
        }
        while (d > 0) out[o++] = digits[--d];
    }
    const char *tail = " FIRED\n";
    while (*tail) out[o++] = *tail++;
    uart_write(0x50000000, out, o);

    while (1) { }
}
