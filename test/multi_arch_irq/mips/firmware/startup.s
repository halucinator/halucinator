/*
 * MIPS32 BE bare-metal reset entry + IRQ trampoline.
 *
 * QEMU mips reset PC is 0xBFC00000 (kseg1 mirror of 0x1FC00000).
 * For the configurable_machine we run, we just place .text at flash
 * base 0x00000000 and the firmware boots at _reset there. The
 * synthetic-IRQ backends jump straight to _irq_entry_simple, which
 * passes the IRQ number in $a0 to IRQ_Handler and returns via $ra.
 */

    .set noreorder
    .set nomacro
    .text

    .globl _reset
    .ent   _reset
_reset:
    /* Zero .bss — uninitialized statics like irq_fired must start
     * as 0, but our raw .bin doesn't bring zero pages with it. */
    la    $t0, _bss_start
    la    $t1, _bss_end
1:  beq   $t0, $t1, 2f
    nop
    sw    $zero, 0($t0)
    addiu $t0, $t0, 4
    b     1b
    nop
2:  la    $sp, _stack_top
    jal   main
    nop
3:  b     3b
    nop
    .end   _reset

    /* Minimal synthetic IRQ trampoline: backend sets $ra =
     * interrupted PC, $a0 = irq id; we save $ra into a known
     * memory slot so IRQ_Handler can clobber freely, call into C,
     * then restore $ra and `jr $ra`. Avoids stack manipulation in
     * case unicorn-MIPS exception delivery interacts badly with
     * the stack pointer. */
    .globl _irq_entry_simple
    .ent   _irq_entry_simple
_irq_entry_simple:
    /* Save the interrupted PC (handed to us in $ra) into $s0, a
     * callee-saved register that IRQ_Handler must preserve per the
     * MIPS o32 ABI. Avoids any memory access during the trampoline,
     * which sidesteps unicorn-MIPS quirks around store handling
     * during the synthetic exception entry. */
    move  $s0, $ra
    jal   IRQ_Handler
    nop
    move  $ra, $s0
    jr    $ra
    nop
    .end   _irq_entry_simple
