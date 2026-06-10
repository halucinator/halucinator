/*
 * MIPS32 big-endian bare-metal IRQ test firmware.
 *
 * MIPS interrupts go through CP0:
 *   - Cause.IP[7:0] holds pending interrupt bits (IP[1:0] are SW,
 *     IP[7:2] are HW IRQs 0..5).
 *   - Status.IM[7:0] are interrupt masks.
 *   - Status.IE is the global IRQ enable.
 *
 * For the IrqController MMIO/CP0 abstraction, halucinator's
 * MipsController writes Cause.IP[N] directly. Backends that don't
 * model exception entry (unicorn) take a synthetic trampoline.
 *
 * On real MIPS, the exception vector lives at EBase+0x180 (general
 * exception handler) when Cause.IV=0; we configure an alternative
 * IRQ entry through a firmware-defined trampoline that the
 * synthetic-entry backends jump to directly.
 */
#include <stdint.h>

#define TEST_IRQ_NUM 5u   /* HW IRQ 5 — Status.IM[7] / Cause.IP[7] */

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

/* Backends without CP0 exception modelling stash the acknowledged
 * IRQ id at this scratch address before jumping to the trampoline
 * (the in-process synthetic-entry path doesn't preserve $a0 reliably
 * across unicorn's emu_start re-entry). Address 0x40000010 sits in
 * RAM (kuseg low) and is reachable without TLB setup. The firmware
 * reads from here so it sees the right interrupt id either way. */
#define IRQ_SCRATCH (*(volatile uint32_t *)0xA0010010u)

__attribute__((used)) void IRQ_Handler(uint32_t intid) {
    /* Prefer the register-passed value when non-zero (real CP0
     * delivery); fall back to the scratch slot for synthetic
     * entry. */
    uint32_t id = intid ? intid : IRQ_SCRATCH;
    irq_number = id;
    irq_fired = 1;
}

__attribute__((used)) void Default_Handler(void) {
    static const char msg[] = "MIPS_FAULT\n";
    uart_write(0xB0000000u, msg, sizeof(msg) - 1);
    while (1) { }
}

void main(void) {
    uart_init(0xB0000000u);

    static const char ready[] = "READY\n";
    uart_write(0xB0000000u, ready, sizeof(ready) - 1);

    /* CP0 Status setup is intentionally skipped: in-process backends
     * don't model CP0 exceptions, and the IrqController takes the
     * synthetic-trampoline path on those. On QEMU+real-MIPS targets
     * we'd want to set Status.IE / Status.IM here. */

    while (!irq_fired) {
        __asm__ volatile ("nop");
    }

    /* Print "IRQ N FIRED\n" piece by piece. The string literal +
     * dynamic-decimal approach used in the cortex-m / arm32 / arm64
     * variants doesn't survive MIPS gcc -Os here (rodata layout
     * issue), so each literal is a separate uart_write call. */
    static const char prefix[] = "IRQ ";
    static const char tail[]   = " FIRED\n";
    uart_write(0xB0000000u, prefix, sizeof(prefix) - 1);

    char num_buf[8];
    uint32_t n = irq_number;
    int d = 0;
    if (n == 0) {
        num_buf[d++] = '0';
    } else {
        char digits[8];
        int i = 0;
        while (n > 0 && i < 8) {
            uint32_t q = 0, t = n;
            while (t >= 10) { t -= 10; q++; }
            digits[i++] = '0' + (n - q * 10);
            n = q;
        }
        while (i > 0) num_buf[d++] = digits[--i];
    }
    uart_write(0xB0000000u, num_buf, d);
    uart_write(0xB0000000u, tail, sizeof(tail) - 1);

    while (1) { }
}
