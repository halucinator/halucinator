/* Copyright 2026 Christopher Wright */

/*
 * Cortex-M3 *WFI* IRQ test firmware — reproduction for upstream issue #31
 * ("Exit WFI sleep state by IRQ injection").
 *
 * Identical to test/multi_arch_irq/cortex_m/firmware/test_irq.c EXCEPT the
 * idle loop uses `WFI` (Wait For Interrupt) instead of a `nop` polling
 * spin. This is the exact scenario the issue reporter hit with Zephyr's
 * `arch_cpu_idle()`:
 *
 *   - main() enables the test IRQ, prints "READY", then halts in WFI.
 *   - The test runner injects the IRQ via hal_dev_irq_trigger AFTER the
 *     CPU is already halted.
 *   - If injection wakes the CPU, IRQ_Handler runs, sets irq_fired, and
 *     main() prints "IRQ 17 FIRED". If WFI is never exited, the firmware
 *     hangs forever and the test times out (bug reproduced).
 *
 * Compare against the polling variant which is known to PASS: that proves
 * the firmware/NVIC-enable/vector wiring is correct and isolates the
 * failure to the "wake a halted CPU" path.
 */
#include <stdint.h>

extern void uart_init(uint32_t uart_id);
extern void uart_write(uint32_t uart_id, const char *buf, uint32_t len);

#define TEST_IRQ_NUM 17

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

    /* Enable our test IRQ in the NVIC so an asserted line wakes WFI. */
    NVIC_ISER0[TEST_IRQ_NUM / 32] = 1u << (TEST_IRQ_NUM % 32);

    static const char ready[] = "READY\n";
    uart_write(0x40000000, ready, sizeof(ready) - 1);

    /* THE difference from the polling test: sleep in WFI. A pending,
     * enabled, sufficiently-prioritised NVIC exception must wake the
     * core here. This is what issue #31 reports as broken when the IRQ
     * is injected via QMP / GDB after the CPU has already halted. */
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
    } else {
        static const char miss[] = "TIMEOUT\n";
        uart_write(0x40000000, miss, sizeof(miss) - 1);
    }

    while (1) { }
}
