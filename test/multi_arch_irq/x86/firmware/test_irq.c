/* Copyright 2026 Christopher Wright */

/*
 * i386 (32-bit x86, flat protected mode) bare-metal IRQ test firmware.
 *
 * On a PC an external interrupt (8259 PIC -> IDT vector) makes the CPU
 * push EFLAGS/CS/EIP and vector to the handler, which returns with
 * `iret`. Unicorn's in-process x86 model delivers no hardware
 * interrupts, so halucinator's X86Pic controller *synthesises* that
 * entry: it pushes the same EIP/CS/EFLAGS frame and sets EIP to the
 * configured `isr_addr`. IRQ_Handler is declared
 * __attribute__((interrupt)) so the compiler emits the matching
 * prologue and an `iret` epilogue that restores EFLAGS (re-enabling IF).
 *
 * Flow: main() prints READY via the intercepted uart_write, sets IF
 * (sti) and spins until the handler fires, then prints "IRQ 7 FIRED".
 */
#include <stdint.h>

#define TEST_IRQ_NUM 7
#define _STR(x) #x
#define STR(x) _STR(x)

/* Intercepted by halucinator's generic.test_uart.TestUART (cdecl:
 * arg0=uart_id, arg1=buf, arg2=len). The bodies just touch a sink so
 * the symbols survive -Os and have a real call site to break on. */
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

volatile uint32_t irq_fired = 0;
volatile uint32_t irq_number = 0;

/* Naked handler: X86Pic vectors here after pushing the EIP/CS/EFLAGS
 * frame. We store the result with immediate-to-memory writes (no GPRs
 * touched) and `iret` to pop the exact 12-byte frame and restore IF.
 * A naked body avoids the compiler's interrupt-attribute prologue,
 * which mis-handled X86Pic's manually-pushed frame. */
__attribute__((naked, used)) void IRQ_Handler(void) {
    __asm__ volatile(
        "movl $" STR(TEST_IRQ_NUM) ", irq_number\n\t"
        "movl $1, irq_fired\n\t"
        "iret\n\t"
        ::: "memory");
}

void main(void) {
    uart_init(0x3F8u);

    static const char ready[] = "READY\n";
    uart_write(0x3F8u, ready, sizeof(ready) - 1);

    /* Unmask interrupts so X86Pic's IF check passes and the tick lands. */
    __asm__ volatile ("sti");

    while (!irq_fired) {
        __asm__ volatile ("pause" ::: "memory");
    }

    /* irq_number is fixed (7); print a single literal so -Os rodata
     * layout stays trivial (see the mips variant's note). */
    static const char fired[] = "IRQ 7 FIRED\n";
    uart_write(0x3F8u, fired, sizeof(fired) - 1);

    while (1) {
        __asm__ volatile ("pause" ::: "memory");
    }
}
