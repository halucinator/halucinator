/*
 * Cortex-M3 IRQ test firmware for HALucinator multi_arch_irq corpus.
 *
 * Boots, prints "READY" to UART, then waits in a polling loop for the
 * ISR to set `irq_fired`. When set, prints "IRQ <N> FIRED" and exits the
 * loop. The test runner injects the IRQ via `hal_dev_irq_trigger -i N`
 * after seeing "READY", then asserts the "IRQ N FIRED" line appears.
 *
 * Stays minimal on purpose:
 *  - Vector table at 0x08000000 with 256 interrupt slots filled with the
 *    same handler (so we can pick any IRQ number 0..239 and still exit).
 *  - uart_init / uart_write / uart_read are bp_handler intercepts —
 *    halucinator routes them through TestUART (the same handler the
 *    multi_arch firmware uses).
 *  - No CMSIS dependency. Direct register layout via volatile pointers.
 */
#include <stdint.h>

/*
 * Test functions intercepted by halucinator's TestUART bp_handler.
 * The bp_handler reads the args and replaces the body. We only ever
 * call them; the implementations below are placeholders.
 */
extern void uart_init(uint32_t uart_id);
extern void uart_write(uint32_t uart_id, const char *buf, uint32_t len);

/*
 * The IRQ that fires the test. Pick a number in the external IRQ
 * range (0..239 on Cortex-M3). We use 17 so it crosses ISPR0 (covers
 * 0..31) — exercises the (N // 32, N % 32) split in the controller.
 */
#define TEST_IRQ_NUM 17

static volatile uint32_t irq_fired = 0;
static volatile uint32_t irq_number = 0;

/* Real implementations are bp_handler stubs — but the firmware needs
 * real out-of-line function bodies so halucinator's TestUART intercept
 * has a non-inlined call site to break on. `noinline` + a side effect
 * the optimiser can't elide keeps each function as its own basic block
 * with a stable address. */
volatile uint32_t _uart_sink;

__attribute__((used, noinline)) void uart_init(uint32_t uart_id) {
    _uart_sink = uart_id;
}
__attribute__((used, noinline)) void uart_write(uint32_t uart_id, const char *buf, uint32_t len) {
    _uart_sink = (uint32_t)(uintptr_t)buf ^ uart_id ^ len;
}

/* The shared external IRQ handler. For HALucinator's purposes any
 * external interrupt entry sets the flag — we don't need per-IRQ
 * handlers since the test triggers exactly one. */
__attribute__((used)) void IRQ_Handler(void) {
    irq_fired = 1;
    irq_number = TEST_IRQ_NUM;
}

/* HardFault: write a sentinel and spin. Test failure if we ever land
 * here. */
__attribute__((used)) void HardFault_Handler(void) {
    static const char msg[] = "HARDFAULT\n";
    uart_write(0x40000000, msg, sizeof(msg) - 1);
    while (1) { }
}

/*
 * NVIC ISER (Interrupt Set-Enable Register). Architectural fixed
 * address. Writing 1 << N to ISER0..15 enables IRQ N at the NVIC
 * controller; without this, even an asserted IRQ line stays masked
 * and the CPU never takes the exception.
 */
#define NVIC_ISER0 ((volatile uint32_t *)0xE000E100)

void main(void) {
    uart_init(0x40000000);

    /* Enable our test IRQ in the NVIC so an asserted line actually
     * pre-empts main's polling loop. */
    NVIC_ISER0[TEST_IRQ_NUM / 32] = 1u << (TEST_IRQ_NUM % 32);

    static const char ready[] = "READY\n";
    uart_write(0x40000000, ready, sizeof(ready) - 1);

    /* Tight polling loop — IRQ_Handler will set irq_fired and the
     * NVIC's exception entry pre-empts main between any two
     * instructions. WFI would be cleaner but adds another execution
     * mode the backends would have to model. */
    while (!irq_fired) {
        __asm__ volatile ("nop");
    }

    if (irq_fired) {
        /* Format "IRQ NN FIRED\n" with NN = decimal irq_number. */
        char out[32];
        const char *p = "IRQ ";
        int o = 0;
        while (*p) out[o++] = *p++;
        /* simple uint to decimal, max 3 digits */
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

    /* Halt the firmware cleanly. The test waits for "IRQ NN FIRED"
     * in the output and then kills halucinator. */
    while (1) { }
}
