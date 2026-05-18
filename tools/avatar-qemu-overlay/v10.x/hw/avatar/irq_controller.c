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
#include "target/arm/cpu.h"
#include "hw/avatar/halucinator_irq_memory.h"
#elif defined(TARGET_MIPS)
#elif defined(TARGET_PPC)
#include "target/ppc/cpu.h"
#include "hw/avatar/halucinator_irq_memory.h"

#endif


#define TYPE_HALUCINATOR_IRQ "halucinator-irq"
#define HALUCINATOR_IRQ(obj) OBJECT_CHECK(HalucinatorIRQState, (obj), TYPE_HALUCINATOR_IRQ)


static void update_irq(HalucinatorIRQState * s){
    int i;
    bool any_irq_active = false;
    bool irq_active = false;
    printf("Active IRQs: ");
    for (i=0; i < s->num_irqs ; i++){
        irq_active = (s->irq_regs[i] & IRQ_N_ACTIVE) && (s->irq_regs[i] & IRQ_N_ENABLED);
        any_irq_active |= irq_active;
        if (irq_active){
             printf("%i, ", i);
        }
    }
    if (s->status_reg & GLOBAL_IRQ_ENABLED){
        // printf("QEMU: Setting Global IRQ %d\n", any_irq_active);
        printf("Setting CPU IRQ\n");
        qemu_set_irq(s->irq, any_irq_active);
    }else{
        // printf("Clearing IRQ as global is not active\n");
        printf("IRQ GLOBAL Not Set\n");
        qemu_set_irq(s->irq, 0);
    }
}


static uint64_t halucinator_irqc_read(void *opaque, hwaddr offset,
                           unsigned size)
{
    uint64_t ret = 0;
    int i;

    // printf("IRQC_READ --- ");
    // printf("Reading Halucinator-IRQ addr 0x%lx: size 0x%x\n", offset, size);
    HalucinatorIRQState *s = HALUCINATOR_IRQ(opaque);
    if (offset == 0){
        ret = s->status_reg & 0xFFFFFFFF;
        // printf("Read IRQ %li returning 0x%08lx\n",offset, ret);
        // update_irq(s);
        return ret;
    }
    else if (offset >= OFFSET_IRQ_N_REGS && \
             offset < s->num_irqs + OFFSET_IRQ_N_REGS){
        for (i=0; i < size; i++){
            ret |= (s->irq_regs[offset - OFFSET_IRQ_N_REGS + i] & 0xFF) << (i * 8);
        }
        // printf("Read IRQ %li returning 0x%08lx\n",offset - OFFSET_IRQ_N_REGS, ret);
        // update_irq(s);
        return ret;
    }

    printf("Invalid Access Returning 0x%08lx\n", ret);
    return ret;
}


static void halucinator_irqc_write(void *opaque, hwaddr offset,
                        uint64_t value, unsigned size)
{
    printf("IRQC_WRITE --- ");
    // printf("Writing Halucinator-IRQ 0x%lx: value 0x%08lx size 0x%x\n", offset, value, size);
    HalucinatorIRQState *s = HALUCINATOR_IRQ(opaque);

    if (offset == 0){
        s->status_reg = (uint32_t)(value & 0xFFFFFFFF);
        update_irq(s);
        return;
    }
    else if (offset >= OFFSET_IRQ_N_REGS && \
             offset < s->num_irqs + OFFSET_IRQ_N_REGS){
        printf("(%li, 0x%02x) ", offset- OFFSET_IRQ_N_REGS, (uint8_t)(value & 0xFF));
        s->irq_regs[offset - OFFSET_IRQ_N_REGS] = (uint8_t)(value & 0xFF);
        // printf("Set IRQ %li to 0x%02x\n", offset - OFFSET_IRQ_N_REGS,
        //         s->irq_regs[offset - OFFSET_IRQ_N_REGS]);
        update_irq(s);
        return;
    }

    printf("Invalid Access Returning\n");
    return;
}


static void irq_handler(void *opaque, int irq, int level)
{
    struct HalucinatorIRQState *s = HALUCINATOR_IRQ(opaque);
    assert(irq < s->num_irqs);


    printf("IRQ_HANDLER --- ");
    if (level){ // Set IRQ Active
        // printf("QEMU: IRQ_HANDLER: Setting IRQ %i ACTIVE\n", irq);
        s->irq_regs[irq] |= IRQ_N_ACTIVE;
    }
    else{ //Clear IRQ Active
        // printf("QEMU: IRQ_HANDLER: Clearing IRQ %i\n", irq);
        s->irq_regs[irq] &= ~IRQ_N_ACTIVE;
    }
    update_irq(s);
}


static void halucinator_irq_clear_irq_setter(Object * obj, Visitor *v, const char *name, void *opaque, Error** errp){
    struct HalucinatorIRQState * s = HALUCINATOR_IRQ(obj);
    int64_t irq_num;
    printf("IRQ_CLEAR --- ");
    visit_type_int(v, name, &irq_num, errp);
    if(s->irq_regs == NULL){
        return;
    }
    if(irq_num < 0 || irq_num >= s->num_irqs){
        return;
    }
    s->irq_regs[irq_num] &= ~IRQ_N_ACTIVE;
    update_irq(s);
}

static void halucinator_irq_set_irq_setter(Object * obj, Visitor *v, const char *name, void *opaque, Error** errp){
    struct HalucinatorIRQState * s = HALUCINATOR_IRQ(obj);
    int64_t irq_num;

    printf("IRQ_SET --- ");
    visit_type_int(v, name, &irq_num, errp);
    if(s->irq_regs == NULL){
        return;
    }
    if(irq_num < 0 || irq_num >= s->num_irqs){
        return;
    }
    s->irq_regs[irq_num] |= IRQ_N_ACTIVE;
    update_irq(s);
}

static void halucinator_irq_enable_irq_setter(Object * obj, Visitor *v, const char *name, void *opaque, Error** errp){
    struct HalucinatorIRQState * s = HALUCINATOR_IRQ(obj);
    int64_t irq_num;
    printf("IRQ_ENABLE --- ");
    visit_type_int(v, name, &irq_num, errp);
    if(s->irq_regs == NULL){
        return;
    }
    if(irq_num < 0 || irq_num >= s->num_irqs){
        return;
    }
    s->irq_regs[irq_num] |= IRQ_N_ENABLED;
    update_irq(s);
}

static void halucinator_irq_disable_irq_setter(Object * obj, Visitor *v, const char *name, void *opaque, Error** errp){
    struct HalucinatorIRQState * s = HALUCINATOR_IRQ(obj);
    int64_t irq_num;
    printf("IRQ_DISABLE --- ");
    visit_type_int(v, name, &irq_num, errp);
    if(s->irq_regs == NULL){
        return;
    }
    if(irq_num < 0 || irq_num >= s->num_irqs){
        return;
    }
    s->irq_regs[irq_num] &= ~IRQ_N_ENABLED;
    update_irq(s);
}

static const MemoryRegionOps halucinator_irq_ops = {
    .read = halucinator_irqc_read,
    .write = halucinator_irqc_write,
    .endianness = DEVICE_NATIVE_ENDIAN,
};

static const Property halucinator_irq_properties[] = {
    DEFINE_PROP_UINT32("num_irqs", HalucinatorIRQState, num_irqs, DEFAULT_NUM_IRQS),
    // DEFINE_PROP_END_OF_LIST(),
};



static void halucinator_irq_realize(DeviceState *dev, Error **errp)
{
    printf("Realizing Halucinator-IRQ\n");
    HalucinatorIRQState *s = HALUCINATOR_IRQ(dev);

    SysBusDevice *sbd = SYS_BUS_DEVICE(s);
    sysbus_init_irq(sbd, &s->irq);
    memory_region_init_io(&s->iomem, OBJECT(s), &halucinator_irq_ops, s,
                          "halucinator-irq", s->num_irqs+OFFSET_IRQ_N_REGS);
    sysbus_init_mmio(sbd, &s->iomem);
    s->status_reg = 0;
    s->irq_regs = g_new(unsigned char, s->num_irqs);
    qdev_init_gpio_in_named_with_opaque(DEVICE(dev), irq_handler, dev, "IRQ", s->num_irqs);
    bzero(s->irq_regs, s->num_irqs);
    printf("Done Realizing Halucinator-IRQ\n");

}

static void halucinator_irq_unrealize(DeviceState *dev)
{
    HalucinatorIRQState *s = HALUCINATOR_IRQ(dev);
    g_free(s->irq_regs);
}

static void halucinator_irq_class_init(ObjectClass *oc, void *data)
{
    printf("Initializing Halucinator-IRQ 2\n");
    DeviceClass *dc = DEVICE_CLASS(oc);

    dc->realize = halucinator_irq_realize;
    dc->unrealize = halucinator_irq_unrealize;
    device_class_set_props(dc, halucinator_irq_properties);

    object_class_property_add(oc, "set-irq", "int", NULL, halucinator_irq_set_irq_setter, NULL, NULL);
    object_class_property_set_description(oc, "set-irq", "Write only property that sets the specified IRQ");

    object_class_property_add(oc, "clear-irq", "int", NULL, halucinator_irq_clear_irq_setter, NULL, NULL);
    object_class_property_set_description(oc, "set-irq", "Write only property that clears the specified IRQ");

    object_class_property_add(oc, "enable-irq", "int", NULL, halucinator_irq_enable_irq_setter, NULL, NULL);
    object_class_property_set_description(oc, "set-irq", "Write only property that enables the specified IRQ");

    object_class_property_add(oc, "disable-irq", "int", NULL, halucinator_irq_disable_irq_setter, NULL, NULL);
    object_class_property_set_description(oc, "set-irq", "Write only property that disables the specified IRQ");
}

static const TypeInfo halucinator_irq_arm_info = {
    .name          = TYPE_HALUCINATOR_IRQ,
    .parent        = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(HalucinatorIRQState),
    .class_init    = halucinator_irq_class_init
};

static void halucinator_irq_register_types(void)
{
    printf("Halucinator-IRQ: Register types\n");
    type_register_static(&halucinator_irq_arm_info);
}

type_init(halucinator_irq_register_types)
