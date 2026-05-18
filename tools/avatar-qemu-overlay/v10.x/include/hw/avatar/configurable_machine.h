#ifndef AVATAR_CONFIGURABLE_MACHINE_H
#define AVATAR_CONFIGURABLE_MACHINE_H

#include "qom/object.h"

typedef struct ConfigurableMachineState ConfigurableMachineState;

#define TYPE_CONFIGURABLE_MACHINE  MACHINE_TYPE_NAME("configurable")

typedef struct ConfigurableMachineClass ConfigurableMachineClass;
DECLARE_OBJ_CHECKERS(ConfigurableMachineState, ConfigurableMachineClass,
                     CONFIGURABLE_MACHINE, TYPE_CONFIGURABLE_MACHINE)


struct ConfigurableMachineClass {
    MachineClass parent_obj;
};

#endif
