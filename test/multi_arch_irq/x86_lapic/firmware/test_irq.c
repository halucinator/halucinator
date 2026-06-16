/* Copyright 2026 Christopher Wright */

/*
 * i386 IRQ test firmware for the QEMU/avatar (LAPIC) path.
 *
 * The avatar configurable machine now starts flat (4 GiB segments,
 * 32-bit), so this firmware loads high like the unicorn variant. A real
 * injected interrupt arrives through the CPU's local APIC and is
 * dispatched via the IDT, so the firmware installs a small flat GDT
 * (statically initialised), reloads the segment selectors, builds an IDT
 * with a gate for the test vector, and enables the LAPIC. halucinator
 * injects with hal-x86-inject-irq -> apic_deliver_irq(APIC_DM_FIXED, V).
 */
#include <stdint.h>

#define VECTOR 0x20
#define LAPIC_BASE 0xFEE00000u

extern void uart_init(uint32_t uart_id);
extern void uart_write(uint32_t uart_id, const char *buf, uint32_t len);
volatile uint32_t _uart_sink;
__attribute__((used, noinline)) void uart_init(uint32_t id) { _uart_sink = id; }
__attribute__((used, noinline)) void uart_write(uint32_t id, const char *b, uint32_t n) {
    _uart_sink = (uint32_t)(uintptr_t)b ^ id ^ n;
}

volatile uint32_t irq_fired = 0;

/* Flat GDT: null / code(0x08, base 0, 4 GiB, exec-read) /
 * data(0x10, base 0, 4 GiB, read-write). Statically initialised so it is
 * correct and present in the loaded image. */
__attribute__((aligned(8))) static const uint64_t gdt[3] = {
    0x0000000000000000ULL,
    0x00CF9A000000FFFFULL,
    0x00CF92000000FFFFULL,
};

struct idt_entry { uint16_t o0; uint16_t sel; uint8_t z; uint8_t type; uint16_t o1; } __attribute__((packed));
struct dtr { uint16_t limit; uint32_t base; } __attribute__((packed));
static struct idt_entry idt[256];
static struct dtr gdtr, idtr;

/* Naked ISR: flag the IRQ, write LAPIC EOI (offset 0xB0), iret. */
__attribute__((naked, used)) void isr(void) {
    __asm__ volatile(
        "movl $1, irq_fired\n\t"
        "movl $0, 0xFEE000B0\n\t"
        "iret\n\t" ::: "memory");
}

void main(void) {
    uart_init(0x3F8u);
    uart_write(0x3F8u, "READY\n", 6);

    /* Load the GDT and reload CS (far jump to 0x08) + data segs (0x10). */
    gdtr.limit = sizeof(gdt) - 1; gdtr.base = (uint32_t)(uintptr_t)gdt;
    __asm__ volatile("lgdt %0" :: "m"(gdtr));
    __asm__ volatile(
        "ljmp $0x08, $1f\n"
        "1:\n\t"
        "movw $0x10, %%ax\n\t"
        "movw %%ax, %%ds\n\tmovw %%ax, %%es\n\tmovw %%ax, %%ss\n\t"
        "movw %%ax, %%fs\n\tmovw %%ax, %%gs\n\t" ::: "ax");
    uart_write(0x3F8u, "GDT\n", 4);

    /* IDT: 32-bit interrupt gate (0x8E), selector 0x08 -> isr. */
    for (int i = 0; i < 256; i++) { idt[i].o0 = idt[i].sel = idt[i].z = idt[i].type = idt[i].o1 = 0; }
    uint32_t h = (uint32_t)(uintptr_t)isr;
    idt[VECTOR].o0 = h & 0xFFFF; idt[VECTOR].sel = 0x08;
    idt[VECTOR].z = 0; idt[VECTOR].type = 0x8E; idt[VECTOR].o1 = (h >> 16) & 0xFFFF;
    idtr.limit = sizeof(idt) - 1; idtr.base = (uint32_t)(uintptr_t)idt;
    __asm__ volatile("lidt %0" :: "m"(idtr));
    uart_write(0x3F8u, "IDT\n", 4);

    /* Enable the LAPIC: Spurious Interrupt Vector Register (off 0xF0),
     * APIC-enable bit 8 + spurious vector 0xFF. */
    *(volatile uint32_t *)(LAPIC_BASE + 0xF0) = 0x100 | 0xFF;
    uart_write(0x3F8u, "LAP\n", 4);

    __asm__ volatile("sti");
    uart_write(0x3F8u, "STI\n", 4);

    while (!irq_fired) { __asm__ volatile("pause" ::: "memory"); }

    uart_write(0x3F8u, "IRQ 32 FIRED\n", 13);
    while (1) { __asm__ volatile("pause" ::: "memory"); }
}
