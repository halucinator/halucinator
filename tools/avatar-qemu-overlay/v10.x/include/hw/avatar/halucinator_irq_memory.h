#ifndef HW_HALUCINATOR_IRQ_MEMORY_H
#define HW_HALUCINATOR_IRQ_MEMORY_H

#define GLOBAL_IRQ_ENABLED 0x00000001
#define IRQ_N_ACTIVE 0x01
#define IRQ_N_ENABLED 0x80

typedef struct HalucinatorIRQState {
    SysBusDevice parent_obj;
    MemoryRegion iomem;
    uint64_t address;
    uint32_t num_irqs;
    uint64_t request_id;
    unsigned char * irq_regs;
    uint32_t status_reg;
    qemu_irq irq;
} HalucinatorIRQState;

#endif
