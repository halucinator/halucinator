/*
 * Avatar2 helper functions for configurable machines using ARM
 *
 * Copyright (C) 2017 Eurecom
 * Written by Marius Muench
 *
 * This program is free software; you can redistribute it and/or modify it
 * under the terms of the GNU General Public License as published by the
 * Free Software Foundation; either version 2 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful, but WITHOUT
 * ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
 * FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License
 * for more details.
 */

#include "qemu/osdep.h"
#include "exec/address-spaces.h"
#include "exec/gdbstub.h"

#include "internals.h"

#include "hw/avatar/arm_helper.h"
#include "sysemu/cpu-timers.h"	/* For icount_get() */

static int banked_gdb_set_reg(CPUARMState *env, uint8_t *buf, int reg){
    switch (reg) {
    case 0:
        env->banked_r13[bank_number(ARM_CPU_MODE_USR)] = ldl_p(buf); return 4;
    case 1:
        env->banked_r14[bank_number(ARM_CPU_MODE_USR)] = ldl_p(buf); return 4;
    case 2:
        env->fiq_regs[0] = ldl_p(buf); return 4;
    case 3:
        env->fiq_regs[1] = ldl_p(buf); return 4;
    case 4:
        env->fiq_regs[2] = ldl_p(buf); return 4;
    case 5:
        env->fiq_regs[3] = ldl_p(buf); return 4;
    case 6:
        env->fiq_regs[4] = ldl_p(buf); return 4;
    case 7:
        env->banked_r13[bank_number(ARM_CPU_MODE_FIQ)] = ldl_p(buf); return 4;
    case 8:
        env->banked_r14[bank_number(ARM_CPU_MODE_FIQ)] = ldl_p(buf); return 4;
    case 9:
        env->banked_r13[bank_number(ARM_CPU_MODE_IRQ)] = ldl_p(buf); return 4;
    case 10:
        env->banked_r14[bank_number(ARM_CPU_MODE_IRQ)] = ldl_p(buf); return 4;
    case 11:
        env->banked_r13[bank_number(ARM_CPU_MODE_SVC)] = ldl_p(buf); return 4;
    case 12:
        env->banked_r14[bank_number(ARM_CPU_MODE_SVC)] = ldl_p(buf); return 4;
    case 13:
        env->banked_r13[bank_number(ARM_CPU_MODE_ABT)] = ldl_p(buf); return 4;
    case 14:
        env->banked_r14[bank_number(ARM_CPU_MODE_ABT)] = ldl_p(buf); return 4;
    case 15:
        env->banked_r13[bank_number(ARM_CPU_MODE_UND)] = ldl_p(buf); return 4;
    case 16:
        env->banked_r14[bank_number(ARM_CPU_MODE_UND)] = ldl_p(buf); return 4;
    case 17:
        env->banked_spsr[BANK_FIQ] = ldl_p(buf); return 4;
    case 18:
        env->banked_spsr[BANK_IRQ] = ldl_p(buf); return 4;
    case 19:
        env->banked_spsr[BANK_SVC] = ldl_p(buf); return 4;
    case 20:
        env->banked_spsr[BANK_ABT] = ldl_p(buf); return 4;
    case 21:
        env->banked_spsr[BANK_UND] = ldl_p(buf); return 4;
    }
    return 0;
}

static int banked_gdb_get_reg(CPUARMState *env, GByteArray *buf, int reg)
{
    switch(reg){
    case 0:
        return gdb_get_reg32(buf, env->banked_r13[bank_number(ARM_CPU_MODE_USR)]);
    case 1:
        return gdb_get_reg32(buf, env->banked_r14[bank_number(ARM_CPU_MODE_USR)]);
    case 2:
        return gdb_get_reg32(buf, env->fiq_regs[0]);
    case 3:
        return gdb_get_reg32(buf, env->fiq_regs[1]);
    case 4:
        return gdb_get_reg32(buf, env->fiq_regs[2]);
    case 5:
        return gdb_get_reg32(buf, env->fiq_regs[3]);
    case 6:
        return gdb_get_reg32(buf, env->fiq_regs[4]);
    case 7:
        return gdb_get_reg32(buf, env->banked_r13[bank_number(ARM_CPU_MODE_FIQ)]);
    case 8:
        return gdb_get_reg32(buf, env->banked_r14[bank_number(ARM_CPU_MODE_FIQ)]);
    case 9:
        return gdb_get_reg32(buf, env->banked_r13[bank_number(ARM_CPU_MODE_IRQ)]);
    case 10:
        return gdb_get_reg32(buf, env->banked_r14[bank_number(ARM_CPU_MODE_IRQ)]);
    case 11:
        return gdb_get_reg32(buf, env->banked_r13[bank_number(ARM_CPU_MODE_SVC)]);
    case 12:
        return gdb_get_reg32(buf, env->banked_r14[bank_number(ARM_CPU_MODE_SVC)]);
    case 13:
        return gdb_get_reg32(buf, env->banked_r13[bank_number(ARM_CPU_MODE_ABT)]);
    case 14:
        return gdb_get_reg32(buf, env->banked_r14[bank_number(ARM_CPU_MODE_ABT)]);
    case 15:
        return gdb_get_reg32(buf, env->banked_r13[bank_number(ARM_CPU_MODE_UND)]);
    case 16:
        return gdb_get_reg32(buf, env->banked_r14[bank_number(ARM_CPU_MODE_UND)]);
    case 17:
        return gdb_get_reg32(buf, env->banked_spsr[BANK_FIQ]);
    case 18:
        return gdb_get_reg32(buf, env->banked_spsr[BANK_IRQ]);
    case 19:
        return gdb_get_reg32(buf, env->banked_spsr[BANK_SVC]);
    case 20:
        return gdb_get_reg32(buf, env->banked_spsr[BANK_ABT]);
    case 21:
        return gdb_get_reg32(buf, env->banked_spsr[BANK_UND]);
    default:
        break;
    }
    return 0;
}

/* GrammaTech 2023-08-14 Synthetic instruction count register */
static int cpuclk_gdb_set_reg(CPUARMState *env, uint8_t *buf, int reg) {
    /* Write is a no-op */
    info_report("Ignore set of cpuclk");
    return 4;
}

static int cpuclk_gdb_get_reg(CPUARMState *env, GByteArray *buf, int reg) {
    return gdb_get_reg64(buf, icount_get());
}

void avatar_add_banked_registers(ARMCPU *cpu){
    CPUState *cs = CPU(cpu);
    gdb_register_coprocessor(cs, banked_gdb_get_reg, banked_gdb_set_reg,
            22, "arm-banked.xml", 0);

    /* GrammaTech 2023-08-14 Synthetic instruction count register */
    gdb_register_coprocessor(cs, cpuclk_gdb_get_reg, cpuclk_gdb_set_reg,
                             1, "arm-cpuclk.xml", 0);
}
