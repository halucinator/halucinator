/*
 * AArch64 bare-metal IRQ test firmware. Mirror of the ARMv7-A
 * version: boots, programs a GICv2, prints "READY", polls a flag,
 * prints "IRQ N FIRED" once the IRQ vector runs.
 *
 * Single exception level (EL1 / EL2 / EL3 — whichever QEMU drops us
 * in). VBAR_EL1 is set in startup.s. IRQs are unmasked via DAIF.I
 * in main() after GIC + UART setup.
 */
#include <stdint.h>

#ifndef GICD_BASE
#define GICD_BASE 0x08000000UL
#endif
#ifndef GICC_BASE
#define GICC_BASE 0x08010000UL
#endif

#define TEST_IRQ_NUM 33u   /* SPI 1 — same numbering as the arm32 test */

#define GICD_CTLR        0x000u
#define GICD_ISENABLER(n) (0x100u + (n) * 4u)
#define GICD_IPRIORITYR(n) (0x400u + (n) * 4u)
#define GICD_ITARGETSR(n) (0x800u + (n) * 4u)

#define GICC_CTLR 0x000u
#define GICC_PMR  0x004u
#define GICC_BPR  0x008u
#define GICC_IAR  0x00Cu
#define GICC_EOIR 0x010u

#define MMIO32(addr) (*(volatile uint32_t *)(uintptr_t)(addr))

extern void uart_init(uint64_t uart_id);
extern void uart_write(uint64_t uart_id, const char *buf, uint64_t len);

volatile uint64_t _uart_sink;
__attribute__((used, noinline)) void uart_init(uint64_t uart_id) {
    _uart_sink = uart_id;
}
__attribute__((used, noinline)) void uart_write(uint64_t uart_id,
                                                const char *buf,
                                                uint64_t len) {
    _uart_sink = (uint64_t)(uintptr_t)buf ^ uart_id ^ len;
}

static volatile uint32_t irq_fired = 0;
static volatile uint32_t irq_number = 0;

static void gic_init(void) {
    MMIO32(GICD_BASE + GICD_CTLR) = 0;
    MMIO32(GICC_BASE + GICC_CTLR) = 0;

    /* Priority 0xA0 for the test IRQ. */
    {
        uint32_t off = GICD_IPRIORITYR(TEST_IRQ_NUM / 4);
        uint32_t shift = (TEST_IRQ_NUM % 4) * 8;
        uint32_t v = MMIO32(GICD_BASE + off);
        v = (v & ~(0xFFu << shift)) | (0xA0u << shift);
        MMIO32(GICD_BASE + off) = v;
    }

    /* Target CPU 0. */
    {
        uint32_t off = GICD_ITARGETSR(TEST_IRQ_NUM / 4);
        uint32_t shift = (TEST_IRQ_NUM % 4) * 8;
        uint32_t v = MMIO32(GICD_BASE + off);
        v = (v & ~(0xFFu << shift)) | (0x01u << shift);
        MMIO32(GICD_BASE + off) = v;
    }

    /* Enable in distributor. */
    MMIO32(GICD_BASE + GICD_ISENABLER(TEST_IRQ_NUM / 32))
        = 1u << (TEST_IRQ_NUM % 32);

    MMIO32(GICC_BASE + GICC_PMR) = 0xFF;
    MMIO32(GICC_BASE + GICC_BPR) = 0;
    MMIO32(GICD_BASE + GICD_CTLR) = 1;
    MMIO32(GICC_BASE + GICC_CTLR) = 1;
}

__attribute__((used)) void IRQ_Handler(void) {
    uint32_t iar = MMIO32(GICC_BASE + GICC_IAR);
    uint32_t intid = iar & 0x3FFu;
    if (intid != 0x3FFu) {
        irq_number = intid;
        irq_fired = 1;
    }
    MMIO32(GICC_BASE + GICC_EOIR) = iar;
}

__attribute__((used)) void Default_Handler(void) {
    static const char msg[] = "ARM_FAULT\n";
    uart_write(0x50000000UL, msg, sizeof(msg) - 1);
    while (1) { }
}

void main(void) {
    uart_init(0x50000000UL);
    gic_init();

    static const char ready[] = "READY\n";
    uart_write(0x50000000UL, ready, sizeof(ready) - 1);

    /* Unmask IRQs in DAIF. */
    __asm__ volatile ("msr daifclr, #2" ::: "memory");

    while (!irq_fired) {
        __asm__ volatile ("nop");
    }

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
    uart_write(0x50000000UL, out, o);

    while (1) { }
}
