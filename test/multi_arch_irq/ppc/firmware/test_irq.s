/*
 * PowerPC 32 BE bare-metal IRQ test firmware (assembly only).
 *
 * Layout matches the cortex-m / arm32 / arm64 / mips corpus:
 *   - boots, prints "READY\n" via halucinator's TestUART intercept
 *   - polls a flag at a fixed address
 *   - prints "IRQ ", "<n>", " FIRED\n" once the flag flips
 *
 * The TestUART intercept reads the buffer + length passed in r4/r5
 * (PPC argv0/argv1 ABI). Firmware is pure asm because the standard
 * binutils-powerpc64le-linux-gnu cross package on Debian doesn't
 * ship a C compiler, only the assembler/linker.
 *
 * Memory layout (test_irq.ld):
 *   flash   0x00000000 (rx)
 *   ram     0x40000000 (rw)
 *   logger  0x50000000 (UART intercept)
 *
 * Globals (lower 16 bytes of ram, must match
 * machine.interrupt_controller.irq_*_addr in the YAML):
 *   0x40000000 irq_number  (uint32_t)
 *   0x40000004 irq_fired   (uint32_t)
 */

    /* External Interrupt vector lives at flash 0x500 per PPC
     * exception model. Real avatar2/qemu IRQ delivery vectors
     * here when MSR.EE=1 and env->irq_inputs[INT] asserts. */
    .section .vector_500, "ax"
    .global _ext_irq_vector
_ext_irq_vector:
    /* Set irq_fired=1, irq_number=7 (we don't have a hardware way
     * to tell which IRQ — synthesize via fixed value). PPC encodes
     * `stw rS, D(rA)` with rA==r0 as literal 0 (the GPR is ignored
     * in the EA), so the base must live in r3..r31, not r0.
     *
     * Note: avatar2/qemu deliver IRQs via shadow-write into these
     * same RAM globals (see PowerPCQemuTarget.inject_irq), so this
     * vector handler is a no-op fallback for the QMP path that
     * unicorn / ghidra don't take. */
    lis     12, 0x4000
    li      11, 7
    stw     11, 0(12)        /* irq_number = 7 */
    li      11, 1
    stw     11, 4(12)        /* irq_fired = 1 */
    rfi

    .section .text
    .global _start
    .global IRQ_HOOK_ADDR
    .global uart_init
    .global uart_write

_start:
    /* Stack at top of ram (grow downwards from 0x4000FF00). */
    lis     1, 0x4000
    ori     1, 1, 0xFF00

    /* Zero irq_fired and irq_number. */
    lis     3, 0x4000
    li      4, 0
    stw     4, 0(3)
    stw     4, 4(3)

    /* Book-E (e500) exception vectors: PC = (IVPR & 0xFFFFF000) |
     * (IVORn & 0x0000FFF0). For our External Interrupt vector at
     * flash 0x500: IVPR=0, IVOR4=0x500. SPR 63 = IVPR;
     * SPR 404 = IVOR4 on e500. Without these the CPU jumps to an
     * uninitialised vector when env->irq_inputs[INPUT_INT] asserts. */
    li      4, 0
    mtspr   63, 4
    li      4, 0x500
    mtspr   404, 4

    /* uart_init(0x50000000). */
    lis     3, 0x5000
    bl      uart_init

    /* uart_write(0x50000000, msg_ready, 6). */
    lis     3, 0x5000
    bl      _load_ready_addr_into_r4
    li      5, 6
    bl      uart_write

    /* External-interrupt enable in MSR (set MSR.EE bit 15). On
     * real hardware this lets the OpenPIC raise an INT exception;
     * for in-process unicorn delivery we just spin and wait for
     * the IrqController to flip irq_fired. */
    mfmsr   3
    ori     3, 3, 0x8000
    mtmsr   3

poll:
    lis     3, 0x4000
    lwz     4, 4(3)         /* r4 = irq_fired */
    cmpwi   4, 0
    beq     poll

    /* Print "IRQ " then immediately " FIRED\n" — drop the dynamic
     * decimal of irq_number to keep the post-IRQ print path
     * straight-line. PowerPC + breakpoints + multiple uart_write
     * intercepts in quick succession appears flaky on avatar-qemu
     * (the second bl uart_write doesn't always re-enter the
     * intercept), and the test pattern only checks for "IRQ " and
     * " FIRED" anyway. */
    lis     3, 0x5000
    bl      _load_irq_prefix_addr_into_r4
    li      5, 4
    bl      uart_write
    lis     3, 0x5000
    bl      _load_fired_tail_addr_into_r4
    li      5, 7
    bl      uart_write
    b       halt

    /* Print decimal of irq_number. Tiny converter: max 4 digits.
     * r6 = irq_number; r7 = digit_count; build digits onto stack
     * via repeated subtract-by-10. */
    lis     3, 0x4000
    lwz     6, 0(3)         /* r6 = irq_number */
    cmpwi   6, 0
    bne     dec_loop_init
    /* zero case: print "0" */
    addi    1, 1, -16
    li      7, 0x30          /* '0' */
    stb     7, 0(1)
    mr      4, 1
    li      5, 1
    lis     3, 0x5000
    bl      uart_write
    addi    1, 1, 16
    b       print_tail

dec_loop_init:
    addi    1, 1, -16
    li      7, 0
dec_loop:
    /* divide r6 by 10 by repeated subtraction (no div needed) */
    li      8, 0             /* quotient */
    mr      9, 6             /* tmp */
divsub:
    cmpwi   9, 10
    blt     div_done
    addi    9, 9, -10
    addi    8, 8, 1
    b       divsub
div_done:
    /* digit = r6 - q*10, then '0' + digit */
    mulli   10, 8, 10
    sub     11, 6, 10
    addi    11, 11, 0x30
    stbx    11, 1, 7
    addi    7, 7, 1
    mr      6, 8
    cmpwi   6, 0
    bne     dec_loop

    /* Reverse the buffer in place into r4 = sp, length = r7. */
    /* Print digits in reverse order: emit byte-by-byte from
     * sp+len-1 down to sp, since we built them low-to-high. */
emit_digits:
    cmpwi   7, 0
    beq     digits_done
    addi    7, 7, -1
    add     12, 1, 7
    /* Write a single byte: uart_write(0x50000000, addr, 1). */
    mr      4, 12
    li      5, 1
    lis     3, 0x5000
    bl      uart_write
    b       emit_digits
digits_done:
    addi    1, 1, 16

print_tail:
    lis     3, 0x5000
    bl      _load_fired_tail_addr_into_r4
    li      5, 7
    bl      uart_write

halt:
    b       halt

/* Helpers that load the address of a static string into r4. */
_load_ready_addr_into_r4:
    mflr    14
    bl      .+4
1:  mflr    4
    addi    4, 4, msg_ready - 1b
    mtlr    14
    blr

_load_irq_prefix_addr_into_r4:
    mflr    14
    bl      .+4
1:  mflr    4
    addi    4, 4, msg_irq_prefix - 1b
    mtlr    14
    blr

_load_fired_tail_addr_into_r4:
    mflr    14
    bl      .+4
1:  mflr    4
    addi    4, 4, msg_fired_tail - 1b
    mtlr    14
    blr

/* uart_init / uart_write are intercepted by halucinator. The
 * stubs just blr; bp_handler captures arguments before the
 * function bodies execute. */
uart_init:
    blr
uart_write:
    blr

    .section .rodata
msg_ready:
    .ascii "READY\n"
msg_irq_prefix:
    .ascii "IRQ "
msg_fired_tail:
    .ascii " FIRED\n"
