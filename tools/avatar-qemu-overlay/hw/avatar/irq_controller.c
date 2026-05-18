// Copyright 2021 National Technology & Engineering Solutions of Sandia, LLC 
// (NTESS). Under the terms of Contract DE-NA0003525 with NTESS, 
// the U.S. Government retains certain rights in this software.

#include "qemu/osdep.h"
#include "hw/qdev-properties.h"
#include "qemu/error-report.h"
#include "qapi/visitor.h"

#include "hw/sysbus.h"
#include "hw/irq.h"
#include "qapi/error.h"
#include <regex.h>

#define DEFAULT_NUM_IRQS 64
#define OFFSET_IRQ_N_REGS 4


#if defined(TARGET_ARM) || defined(TARGET_AARCH64)
#  include "target/arm/cpu.h"
#  include "hw/avatar/halucinator_irq_memory.h"
#elif defined(TARGET_MIPS)
#  include "target/mips/cpu.h"
#  include "hw/avatar/halucinator_irq_memory.h"
#elif defined(TARGET_PPC)
#  include "target/ppc/cpu.h"
#  include "hw/avatar/halucinator_irq_memory.h"
#else
# error "halucinator_irq: unsupported architecture"
#endif

#define TYPE_HALUCINATOR_IRQ "halucinator-irq"
#define HALUCINATOR_IRQ(obj) OBJECT_CHECK(HALucinatorIRQState, (obj), TYPE_HALUCINATOR_IRQ)


static void update_irq(HALucinatorIRQState * s){
    int i;
    bool any_irq_active = false;
    bool irq_active = false;
    for (i=0; i < s->num_irqs ; i++){
        irq_active = (s->irq_regs[i] & IRQ_N_ACTIVE) && (s->irq_regs[i] & IRQ_N_ENABLED);
        any_irq_active |= irq_active;
        if (irq_active){
            printf("QEMU: IRQ Active %i: %i\n", i, s->irq_regs[i]);
        }
    }
    if (s->status_reg & GLOBAL_IRQ_ENABLED){
        printf("QEMU: Setting Global IRQ %d\n", any_irq_active);
        qemu_set_irq(s->irq, any_irq_active);
    }else{
        printf("QEMU: Clearing IRQ as global is not active\n");
        qemu_set_irq(s->irq, 0);
    }
}

static uint64_t halucinator_irqc_read(void *opaque, hwaddr offset,
                           unsigned size)
{
    uint64_t ret = 0;

    printf("QEMU: Reading Halucinator-IRQ 0x%lx: size 0x%x\n", offset, size);
    HALucinatorIRQState *s = HALUCINATOR_IRQ(opaque);
    if (offset == 0){
        ret = s->status_reg & 0xFFFFFFFF;
        printf("QEMU: Read IRQ %li returning 0x%08lx\n",offset - OFFSET_IRQ_N_REGS, ret);
        update_irq(s);
        return ret;
    }
    else if (offset >= OFFSET_IRQ_N_REGS && \
             offset < s->num_irqs + OFFSET_IRQ_N_REGS){
        ret = s->irq_regs[offset - OFFSET_IRQ_N_REGS] & 0xFF;
        printf("QEMU: Read IRQ %li returning 0x%08lx\n",offset - OFFSET_IRQ_N_REGS, ret);
        update_irq(s);
        return ret;
    }
    
    printf("QEMU: Invalid Access Returning 0x%08lx\n", ret);
    return ret;
}

static void halucinator_irqc_write(void *opaque, hwaddr offset,
                        uint64_t value, unsigned size)
{
    printf("QEMU: Writing Halucinator-IRQ 0x%lx: value 0x%08lx size 0x%x\n", offset, value, size);
    HALucinatorIRQState *s = HALUCINATOR_IRQ(opaque);

    if (offset == 0){
        s->status_reg = (uint32_t)(value & 0xFFFFFFFF);
        update_irq(s);
        return;
    }
    else if (offset >= OFFSET_IRQ_N_REGS && \
             offset < s->num_irqs + OFFSET_IRQ_N_REGS){
        s->irq_regs[offset - OFFSET_IRQ_N_REGS] = (uint8_t)(value & 0xFF);
        printf("QEMU: Set IRQ %li to 0x%02x\n", offset - OFFSET_IRQ_N_REGS, 
                s->irq_regs[offset - OFFSET_IRQ_N_REGS]);
        update_irq(s);
        return;
    }
    
    printf("QEMU: Invalid Access Returning\n");
    return;
}

static void irq_handler(void *opaque, int irq, int level)
{
    struct HALucinatorIRQState *s = HALUCINATOR_IRQ(opaque);
    assert(irq < s->num_irqs);

    if (level){ // Set IRQ Active
        printf("QEMU: IRQ_HANDLER: Setting IRQ %i ACTIVE\n", irq);
        s->irq_regs[irq] |= IRQ_N_ACTIVE;
    }
    else{ //Clear IRQ Active
        printf("QEMU: IRQ_HANDLER: Clearing IRQ %i\n", irq);
        s->irq_regs[irq] &= ~IRQ_N_ACTIVE;
    }
    update_irq(s);
}

static const MemoryRegionOps halucinator_irq_ops = {
    .read = halucinator_irqc_read,
    .write = halucinator_irqc_write,
    .endianness = DEVICE_NATIVE_ENDIAN,
};

static Property halucinator_irq_properties[] = {
    DEFINE_PROP_UINT32("num_irqs", HALucinatorIRQState, num_irqs, DEFAULT_NUM_IRQS),
    DEFINE_PROP_END_OF_LIST(),
};

static void halucinator_irq_realize(DeviceState *dev, Error **errp)
{
    printf("QEMU: Realizing Halucinator-IRQ\n");
    HALucinatorIRQState *s = HALUCINATOR_IRQ(dev);

    
    SysBusDevice *sbd = SYS_BUS_DEVICE(s);
    sysbus_init_irq(sbd, &s->irq);
    memory_region_init_io(&s->iomem, OBJECT(s), &halucinator_irq_ops, s, 
                          "halucinator-irq", s->num_irqs+OFFSET_IRQ_N_REGS);
    sysbus_init_mmio(sbd, &s->iomem);
    s->status_reg = 0;
    s->irq_regs = g_new(unsigned char, s->num_irqs);
    qdev_init_gpio_in_named_with_opaque(DEVICE(dev), irq_handler, dev, "IRQ", s->num_irqs);
    bzero(s->irq_regs, s->num_irqs);

}

static void halucinator_irq_set_irq_setter(Object *obj, Visitor *v,
                                       const char *name, void *opaque,
                                       Error **errp)
{
    struct HALucinatorIRQState *s = HALUCINATOR_IRQ(obj);
    int64_t irq_num;
    visit_type_int(v, name, &irq_num, errp);
    if (s->irq_regs == NULL)
        return;
    if (irq_num < 0 || irq_num >= s->num_irqs){
        return;
    }

    irq_handler(s, irq_num, 1);

}

static void halucinator_irq_clear_irq_setter(Object *obj, Visitor *v,
                                       const char *name, void *opaque,
                                       Error **errp)
{
    struct HALucinatorIRQState *s = HALUCINATOR_IRQ(obj);
    int64_t irq_num;
    visit_type_int(v, name, &irq_num, errp);
    if (s->irq_regs == NULL)
        return;
    if (irq_num < 0 || irq_num >= s->num_irqs){
        return;
    }

    irq_handler(s, irq_num, 0);

}

static void halucinator_irq_enable_irq_setter(Object *obj, Visitor *v,
                                       const char *name, void *opaque,
                                       Error **errp)
{
    struct HALucinatorIRQState *s = HALUCINATOR_IRQ(obj);
    int64_t irq_num;
    visit_type_int(v, name, &irq_num, errp);
    if (s->irq_regs == NULL)
        return;
    if (irq_num < 0 || irq_num >= s->num_irqs){
        return;
    }

    printf("QEMU: Enabling IRQ %li", irq_num);
    s->irq_regs[irq_num] |= IRQ_N_ENABLED;
    update_irq(s);

}

static void halucinator_irq_disable_irq_setter(Object *obj, Visitor *v,
                                       const char *name, void *opaque,
                                       Error **errp)
{
    struct HALucinatorIRQState *s = HALUCINATOR_IRQ(obj);
    int64_t irq_num;
    visit_type_int(v, name, &irq_num, errp);
    if (s->irq_regs == NULL)
        return;
    if (irq_num < 0 || irq_num >= s->num_irqs){
        return;
    }

    printf("QEMU: Disabling IRQ %li", irq_num);
    s->irq_regs[irq_num] &= ~IRQ_N_ENABLED;
    update_irq(s);

}


static void halucinator_irq_unrealize(DeviceState *dev)
{
    HALucinatorIRQState *s = HALUCINATOR_IRQ(dev);
    g_free(s->irq_regs);
}

static void halucinator_irq_class_init(ObjectClass *oc, void *data)
{
    printf("QEMU: Initializing Halucinator-IRQ\n");
    DeviceClass *dc = DEVICE_CLASS(oc);

    dc->realize = halucinator_irq_realize;
    dc->unrealize = halucinator_irq_unrealize;
    device_class_set_props(dc, halucinator_irq_properties);
    // dc->props_ = halucinator_irq_properties;

    // These are used to enable the QMP interface to set and disable interrupts using the qom-set
    // command
    object_class_property_add(oc, "set-irq", "int", NULL, halucinator_irq_set_irq_setter, 
                              NULL, NULL);
    object_class_property_set_description(oc, "set-irq",
                                        "Write only property that sets the specified irq");

    object_class_property_add(oc, "clear-irq", "int", NULL, halucinator_irq_clear_irq_setter, 
                              NULL, NULL);
    object_class_property_set_description(oc, "clear-irq",
                                          "Write only property that clears the specified irq");

    object_class_property_add(oc, "enable-irq", "int", NULL, halucinator_irq_enable_irq_setter, 
                              NULL, NULL);
    object_class_property_set_description(oc, "enable-irq", 
                                          "Write only property that enables the specified irq");
    object_class_property_add(oc, "disable-irq", "int", NULL, 
                                         halucinator_irq_disable_irq_setter, NULL, NULL);
    object_class_property_set_description(oc, "disable-irq",
                                          "Write only property that disables the specified irq");

}

static const TypeInfo halucinator_irq_info = {
    .name          = TYPE_HALUCINATOR_IRQ,
    .parent        = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(HALucinatorIRQState),
    .class_init    = halucinator_irq_class_init
};

static void halucinator_irq_register_types(void)
{
    printf("QEMU: Halucinator-IRQ: Register types\n");
    type_register_static(&halucinator_irq_info);
}

type_init(halucinator_irq_register_types)
