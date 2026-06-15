/* Copyright 2026 Christopher Wright */

/*
 * Cortex-M3 firmware — reproduction for upstream issue #31 scenario #1
 * ("Synchronous MMIO write" deadlock).
 *
 * Flow:
 *   1. uart_init, enable IRQ 17 in the NVIC, print "READY".
 *   2. Write to a peripheral register (TRIGGER_REG). HALucinator forwards
 *      that MMIO write to the Python IrqOnWritePeripheral, whose hw_write
 *      handler SYNCHRONOUSLY calls peripheral_server.inject_irq(17) — i.e.
 *      issues the avatar QMP inject from inside the MMIO handler, exactly
 *      like the issue reporter.
 *   3. Print "WROTE" (only reached if the store returned, i.e. no deadlock).
 *   4. Idle in WFI until irq_fired, then print "IRQ 17 FIRED".
 *
 * Expected (bug present): the vCPU thread holds the QEMU BQL while it
 * waits for the forwarded write to be serviced; the QMP inject inside the
 * handler can't be dispatched -> deadlock. We see "READY" and then nothing
 * (no "WROTE", no "FIRED"): the test times out. That is issue #31.
 */
#include <stdint.h>

extern void uart_init(uint32_t uart_id);
extern void uart_write(uint32_t uart_id, const char *buf, uint32_t len);

#define TEST_IRQ_NUM 17

/* MMIO register backed by the Python IrqOnWritePeripheral. Sits just past
 * the 0x40000000+0x20000000 logger region used for UART. */
#define TRIGGER_REG ((volatile uint32_t *)0x60000000)

static volatile uint32_t irq_fired = 0;
static volatile uint32_t irq_number = 0;

volatile uint32_t _uart_sink;

__attribute__((used, noinline)) void uart_init(uint32_t uart_id) {
    _uart_sink = uart_id;
}
__attribute__((used, noinline)) void uart_write(uint32_t uart_id, const char *buf, uint32_t len) {
    _uart_sink = (uint32_t)(uintptr_t)buf ^ uart_id ^ len;
}

__attribute__((used)) void IRQ_Handler(void) {
    irq_fired = 1;
    irq_number = TEST_IRQ_NUM;
}

__attribute__((used)) void HardFault_Handler(void) {
    static const char msg[] = "HARDFAULT\n";
    uart_write(0x40000000, msg, sizeof(msg) - 1);
    while (1) { }
}

#define NVIC_ISER0 ((volatile uint32_t *)0xE000E100)

void main(void) {
    uart_init(0x40000000);
    NVIC_ISER0[TEST_IRQ_NUM / 32] = 1u << (TEST_IRQ_NUM % 32);

    static const char ready[] = "READY\n";
    uart_write(0x40000000, ready, sizeof(ready) - 1);

    /* Synchronous IRQ assertion from inside an MMIO write handler. */
    *TRIGGER_REG = 1;

    static const char wrote[] = "WROTE\n";
    uart_write(0x40000000, wrote, sizeof(wrote) - 1);

    while (!irq_fired) {
        __asm__ volatile ("wfi");
    }

    if (irq_fired) {
        char out[32];
        const char *p = "IRQ ";
        int o = 0;
        while (*p) out[o++] = *p++;
        uint32_t n = irq_number;
        char digits[4];
        int d = 0;
        if (n == 0) {
            digits[d++] = '0';
        } else {
            while (n > 0 && d < 4) { digits[d++] = '0' + (n % 10); n /= 10; }
        }
        while (d > 0) out[o++] = digits[--d];
        const char *tail = " FIRED\n";
        while (*tail) out[o++] = *tail++;
        uart_write(0x40000000, out, o);
    }

    while (1) { }
}
