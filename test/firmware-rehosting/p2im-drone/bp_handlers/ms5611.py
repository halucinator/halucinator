import logging
import types
import sys
import random

from halucinator.bp_handlers.bp_handler import BPHandler, bp_handler

log = logging.getLogger("ms5611")
log.setLevel(logging.ERROR)

class MS5611(BPHandler):
    ''' handlers for p2im-drone firmware '''

    #
    # Intercept MS5611_Init to insert coefficient values that otherwise
    # would be read from PROM
    #
    @bp_handler(['MS5611_Init'])
    def MS5611_Init(self, qemu, bp_addr):
        '''
            MS5611_Init break point handler
        '''
        print("MS5611_Init")
        MS5611_Coefficients = [
           0,                   # reserved
           40127,               # [1]: Pressure sensitivity | SENSt1
           36924,               # [2]: Pressure offset | OFFt1
           23317,               # [3]: Temperature coefficient of the pressure sensitivity | TCS
           23282,               # [4]: Temperature coefficient of the pressure offset | TCO
           33464,               # [5]: Reference temperature | Tref
           28312,               # [6]: Temperature coefficient of the temperature | TEMPSENS 
        ]
    
        # Set MS5611_Coefficient[0]
        coeff0_ptr = 0x200002a4
        qemu.write_memory(coeff0_ptr, 2, MS5611_Coefficients[0])
        buf = qemu.read_memory(coeff0_ptr, 1, 2, raw=True)
#        log.info(f"set coefficient 0 to {hex(int.from_bytes(buf, 'little'))}.")

        # Set MS5611_Coefficient[1]
        coeff1_ptr = coeff0_ptr + 2
        qemu.write_memory(coeff1_ptr, 2, MS5611_Coefficients[1])
        buf = qemu.read_memory(coeff1_ptr, 1, 2, raw=True)
#        log.info(f"set coefficient 1 to {hex(int.from_bytes(buf, 'little'))}.")

        # Set MS5611_Coefficient[2]
        coeff2_ptr = coeff0_ptr + 4
        qemu.write_memory(coeff2_ptr, 2, MS5611_Coefficients[2])
        buf = qemu.read_memory(coeff2_ptr, 1, 2, raw=True)
#        log.info(f"set coefficient 2 to {hex(int.from_bytes(buf, 'little'))}.")

        # Set MS5611_Coefficient[3]
        coeff3_ptr = coeff0_ptr + 6
        qemu.write_memory(coeff3_ptr, 2, MS5611_Coefficients[3])
        buf = qemu.read_memory(coeff3_ptr, 1, 2, raw=True)
#        log.info(f"set coefficient 3 to {hex(int.from_bytes(buf, 'little'))}.")

        # Set MS5611_Coefficient[4]
        coeff4_ptr = coeff0_ptr + 8
        qemu.write_memory(coeff4_ptr, 2, MS5611_Coefficients[4])
        buf = qemu.read_memory(coeff4_ptr, 1, 2, raw=True)
#        print(f"set coefficient 4 to {hex(int.from_bytes(buf, 'little'))}.")

        # Set MS5611_Coefficient[5]
        coeff5_ptr = coeff0_ptr + 10
        qemu.write_memory(coeff5_ptr, 2, MS5611_Coefficients[5])
        buf = qemu.read_memory(coeff5_ptr, 1, 2, raw=True)
#        log.info(f"set coefficient 5 to {hex(int.from_bytes(buf, 'little'))}.")

        # Set MS5611_Coefficient[6]
        coeff6_ptr = coeff0_ptr + 12
        qemu.write_memory(coeff6_ptr, 2, MS5611_Coefficients[6])
        buf = qemu.read_memory(coeff6_ptr, 1, 2, raw=True)
#        log.info(f"set coefficient 6 to {hex(int.from_bytes(buf, 'little'))}.")

        return True, 0


    #
    # Intercept MS5611_Init to insert ADC output values
    #
    @bp_handler(['MS5611_ReadADC'])
    def MS5611_ReadADC(self, qemu, bp_addr):
        '''
            MS5611_ReadADC break point handler
        '''
        #log.debug("MS5611_ReadADC")

        #
        # Determine whether this is a temperature or a pressure reading
        press_ready_ptr       = 0x200002c8
        temp_ready_ptr        = 0x200002c9
        temp_ready = int.from_bytes(qemu.read_memory_bytes(temp_ready_ptr, 1), 'little')
        press_ready = int.from_bytes(qemu.read_memory_bytes(press_ready_ptr, 1), 'little')
        if (temp_ready + press_ready) == 0:
            nominal_temperature = 8569150
            temperature_difference = random.randrange(-1000,3000)
            reading = nominal_temperature + temperature_difference
            print(f"ReadADC: temperature reading {reading}")
        else: 
            nominal_pressure = 9085466
            pressure_difference = random.randrange(-10000,30000)
            reading = nominal_pressure + pressure_difference
            print(f"ReadADC: pressure reading {reading}")
        qemu.regs.r0 = reading
        return True, reading


