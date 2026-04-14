import types
import sys
import random
import struct
from os import access, R_OK
from os.path import isfile
from os.path import getmtime

from halucinator import hal_log
from halucinator.bp_handlers.bp_handler import BPHandler, bp_handler
logger = hal_log.getHalLogger()

# File that contains drone arguments, in this format (with nominal values)
#  Temp      Pressure  ARM HOLD Throttle   Pitch  Roll  Yaw
# 8569150    9154000    1   0    1000.0     0.0   0.0   0.0
argfile = "/home/haluser/project/argfile.txt"

# Initialize last-modified time for above file
lastmtime = 0

# Set the range of variation for accel/gyro/mag readings (not currently used)
#valrange = 750
valrange = 2

# Initialize drone argument variables
# Temperature and pressure inputs to replace the MS6511 peripheral.
temperature = 8569150
pressure = 9085466
# Joystick throttle controls
motor_arm = 0
alt_hold = 0
throttle = 0.0
pitch_set_point = 0.0
roll_set_point = 0.0
yaw_set_point = 0.0

#def updated_arguments ():
#    '''
#    Load drone arguments from designated file, if it exists.
#    '''
#
#    global temperature
#    global pressure
#    global motor_arm
#    global alt_hold
#    global throttle
#    global pitch_set_point
#    global roll_set_point
#    global yaw_set_point
#
#    if isfile(argfile) and access(argfile, R_OK):
#        with open(argfile, 'r') as infile:
#            #data = infile.read().split()
#            lines = infile.readlines()
#        for line in lines:
#            if line.startswith('#'):
#                continue
#            data = line.split()
#            break
#        if len(data) == 8:
#            #
#            # Just setting the globals here, we will write the values
#            # to program memory later in the calling function (because
#            # it has addresses already defined). TODO just move this to class.
#            temperature = int(data[0])
#            pressure    = int(data[1])
#            motor_arm   = int(data[2])
#            alt_hold    = int(data[3])
#            throttle    = float(data[4])
#            pitch_set_point = float(data[5])
#            roll_set_point  = float(data[6])
#            yaw_set_point   = float(data[7])
#
#            # Constrain inputs as in MSP_SetRawRC_Callback
#            throttle_limit = 10.0
#            pitch_set_point -= throttle_limit
#            pitch_set_point = max(pitch_set_point, -throttle_limit)
#            pitch_set_point = min(pitch_set_point, throttle_limit)
#            roll_set_point -= throttle_limit
#            roll_set_point = max(roll_set_point, -throttle_limit)
#            roll_set_point = min(roll_set_point, throttle_limit)
#            yaw_set_point -= 180.0
#
#            print (f"\nreloaded arguments:")
#            print (f"temperature/pressure: {temperature}/{pressure}")
#            print (f"arm/hold/throttle: {motor_arm}/{alt_hold}/{throttle}")
#            print (f"Set points (pitch/roll/yaw): {pitch_set_point}/{roll_set_point}/{yaw_set_point}")
#            return True
#        else:
#            print ("update_arguments: wrong length")
#    else:
#        print ("update_arguments: no arg file!")
#    return False


class MS5611(BPHandler):
    ''' handlers for p2im-drone firmware '''

    def __init__(self):
        self.MS5611_Coefficients = [
           0,                   # reserved
           40127,               # [1]: Pressure sensitivity | SENSt1
           36924,               # [2]: Pressure offset | OFFt1
           23317,               # [3]: Temperature coefficient of the pressure sensitivity | TCS
           23282,               # [4]: Temperature coefficient of the pressure offset | TCO
           33464,               # [5]: Reference temperature | Tref
           28312,               # [6]: Temperature coefficient of the temperature | TEMPSENS 
        ]
        self.coeff0_ptr = 0x200002a4
        self.coeff1_ptr = self.coeff0_ptr + 2
        self.coeff2_ptr = self.coeff0_ptr + 4
        self.coeff3_ptr = self.coeff0_ptr + 6
        self.coeff4_ptr = self.coeff0_ptr + 8
        self.coeff5_ptr = self.coeff0_ptr + 10
        self.coeff6_ptr = self.coeff0_ptr + 12
        self.press_ready_ptr = 0x200002c8
        self.temp_ready_ptr = 0x200002c9




    #
    # Intercept MS5611_Init to insert coefficient values that otherwise
    # would be read from PROM
    #
    @bp_handler(['MS5611_Init'])
    def MS5611_Init(self, qemu, bp_addr):
        '''
            MS5611_Init break point handler
        '''
        logger.info("MS5611_Init")
        qemu.write_memory(self.coeff0_ptr, 2, self.MS5611_Coefficients[0])
        qemu.write_memory(self.coeff1_ptr, 2, self.MS5611_Coefficients[1])
        qemu.write_memory(self.coeff2_ptr, 2, self.MS5611_Coefficients[2])
        qemu.write_memory(self.coeff3_ptr, 2, self.MS5611_Coefficients[3])
        qemu.write_memory(self.coeff4_ptr, 2, self.MS5611_Coefficients[4])
        qemu.write_memory(self.coeff5_ptr, 2, self.MS5611_Coefficients[5])
        qemu.write_memory(self.coeff6_ptr, 2, self.MS5611_Coefficients[6])
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
        temp_ready = int.from_bytes(qemu.read_memory_bytes(self.temp_ready_ptr, 1), 'little')
        press_ready = int.from_bytes(qemu.read_memory_bytes(self.press_ready_ptr, 1), 'little')

        if (temp_ready + press_ready) == 0:
            reading = temperature
            # no reason to report temperature, I never change it
            #print(f"ReadADC: temperature reading {reading}")
        else:
            reading = pressure
            #print(f"ReadADC: pressure reading {reading}")
        qemu.regs.r0 = reading
        return True, reading



class MPU9250(BPHandler):
    ''' handlers for p2im-drone firmware '''

    # Set addresses to be used later
    def __init__(self):
        self.motor_arm_ptr = 0x20000b38
        self.alt_hold_ptr  = 0x20000b39
        self.throttle_ptr  = 0x20000b3c

        pitch_base = 0x200000a8
        roll_base = 0x20000104
        yaw_base = 0x20000160

        self.pitch_set_point_ptr = pitch_base + 28
        self.roll_set_point_ptr = roll_base + 28
        self.yaw_set_point_ptr = yaw_base + 28

        self.accel_data_x_ptr = 0x20000258
        self.accel_data_y_ptr = 0x2000025c
        self.accel_data_z_ptr = 0x20000260
        self.gyro_data_x_ptr = 0x2000026c
        self.gyro_data_y_ptr = 0x20000270
        self.gyro_data_z_ptr = 0x20000274
        self.mag_data_x_ptr = 0x2000026c
        self.mag_data_y_ptr = 0x20000270
        self.mag_data_z_ptr = 0x20000274


    def load_arguments (self, qemu):
        '''
        Load drone arguments from designated file, if it exists.
        '''
        global temperature
        global pressure
        global motor_arm
        global alt_hold
        global throttle
        global pitch_set_point
        global roll_set_point
        global yaw_set_point

        if isfile(argfile) and access(argfile, R_OK):
            with open(argfile, 'r') as infile:
                #data = infile.read().split()
                lines = infile.readlines()
            for line in lines:
                if line.startswith('#'):
                    continue
                data = line.split()
                break
            if len(data) == 8:
                #
                # Just setting the globals here, we will write the values
                # to program memory later in the calling function (because
                # it has addresses already defined). TODO just move this to class.
                temperature = int(data[0])
                pressure    = int(data[1])
                motor_arm   = int(data[2])
                alt_hold    = int(data[3])
                throttle    = float(data[4])
                pitch_set_point = float(data[5])
                roll_set_point  = float(data[6])
                yaw_set_point   = float(data[7])

                # Constrain inputs as in MSP_SetRawRC_Callback
                throttle_limit = 10.0
                pitch_set_point -= throttle_limit
                pitch_set_point = max(pitch_set_point, -throttle_limit)
                pitch_set_point = min(pitch_set_point, throttle_limit)
                roll_set_point -= throttle_limit
                roll_set_point = max(roll_set_point, -throttle_limit)
                roll_set_point = min(roll_set_point, throttle_limit)
                yaw_set_point -= 180.0

                # Write values to firmware memory
                #
                # Taking special care with how motor_arm and alt_hold are written, since they are byte 
                # booleans, and I'm not sure the system  is reacting properly.
                # Note: When creating bytearray, it is always initializd to 0, so no need to set if
                #       the desired value is Null.
                # When disarming, reset the motor txf struct, so the motor readings fall back to zero
                motor_arm_bytes = bytearray(1)
                msp_txf_motor_ptr = 0x20000b6f
                if motor_arm == 0:
                    msp_txf_motor_bytes = bytearray(8)
                    qemu.write_memory_bytes(msp_txf_motor_ptr, msp_txf_motor_bytes)
                else:
                    motor_arm_bytes[0] = motor_arm
                qemu.write_memory_bytes(self.motor_arm_ptr, motor_arm_bytes)

                alt_hold_bytes = bytearray(1)
                if alt_hold != 0:
                    alt_hold_bytes[0] = alt_hold
                qemu.write_memory_bytes(self.alt_hold_ptr, alt_hold_bytes)

                qemu.write_memory_bytes(self.throttle_ptr, bytearray(struct.pack("=f", throttle)))
                qemu.write_memory_bytes(self.pitch_set_point_ptr, bytearray(struct.pack("=f", pitch_set_point)))
                qemu.write_memory_bytes(self.roll_set_point_ptr, bytearray(struct.pack("=f", roll_set_point)))
                qemu.write_memory_bytes(self.yaw_set_point_ptr, bytearray(struct.pack("=f", yaw_set_point)))
                logger.info (f"\nreloaded arguments:")
                logger.info (f"temperature/pressure: {temperature}/{pressure}")
                logger.info (f"arm/hold/throttle: {motor_arm}/{alt_hold}/{throttle}")
                logger.info (f"Set points (pitch/roll/yaw): {pitch_set_point}/{roll_set_point}/{yaw_set_point}")
                return True
            else:
                logger.error ("load_arguments: wrong length")
        else:
            logger.error ("load_arguments: no arg file!")
        return False


    #
    # Intercept MPU9250_Init to insert coefficient values that otherwise
    # would be read from PROM
    #
    @bp_handler(['MPU9250_Init'])
    def MPU9250_Init(self, qemu, bp_addr):
        '''
            MPU9250_Init break point handler
        '''
        logger.info("MPU9250_Init")
        qemu.write_memory(self.motor_arm_ptr, 1, motor_arm)
        qemu.write_memory(self.alt_hold_ptr, 1, alt_hold)
        qemu.write_memory_bytes(self.throttle_ptr, bytearray(struct.pack("=f", throttle)))
        return True, 0


    #
    # Intercept accelerometer to insert ADC output values
    #
    @bp_handler(['MPU9250_ReadAccelData'])
    def MPU9250_ReadAccelData(self, qemu, bp_addr):
        '''
            MPU9250_ReadAccelData break point handler
        '''
        #
        # Set accelerometer readings (x, y, z)
#        x = float(random.randint(-valrange, valrange))
#        y = float(random.randint(-valrange, valrange))
#        z = float(random.randint(-valrange, valrange))
        x = random.gauss(1.0, 1.0)
        y = random.gauss(1.0, 1.0)
        z = random.gauss(1.0, 1.0)
        qemu.write_memory_bytes(self.accel_data_x_ptr, bytearray(struct.pack("=f", x)))
        qemu.write_memory_bytes(self.accel_data_y_ptr, bytearray(struct.pack("=f", y)))
        qemu.write_memory_bytes(self.accel_data_z_ptr, bytearray(struct.pack("=f", z)))
        logger.info(f"accel x/y/z: {x}/{y}/{z}")
        return True, 0


    #
    # Intercept gyroscope to insert ADC output values
    #
    @bp_handler(['MPU9250_ReadGyroData'])
    def MPU9250_ReadGyroData(self, qemu, bp_addr):
        '''
            MPU9250_ReadGyroData break point handler
        '''
        #
        # Set gyroscope readings (x, y, z)
#        x = float(random.randint(-valrange, valrange))
#        y = float(random.randint(-valrange, valrange))
#        z = float(random.randint(-valrange, valrange))
        x = random.gauss(10.0, 15.0)
        y = random.gauss(10.0, 15.0)
        z = random.gauss(10.0, 15.0)
        qemu.write_memory_bytes(self.gyro_data_x_ptr, bytearray(struct.pack("=f", x)))
        qemu.write_memory_bytes(self.gyro_data_y_ptr, bytearray(struct.pack("=f", y)))
        qemu.write_memory_bytes(self.gyro_data_z_ptr, bytearray(struct.pack("=f", z)))
        logger.info(f"gyro x/y/z: {x}/{y}/{z}")
        return True, 0

    #
    # Intercept compass to insert ADC output values
    #
    @bp_handler(['AK8963_ReadData'])
    def AK8963_ReadData(self, qemu, bp_addr):
        '''
            AK8963_ReadData break point handler
        '''
        #
        # Set magnetometer readings (x, y, z)
#        x = float(random.randint(-valrange, valrange))
#        y = float(random.randint(-valrange, valrange))
#        z = float(random.randint(-valrange, valrange))
        x = random.gauss(100.0, 10.0)
        y = random.gauss(100.0, 10.0)
        z = random.gauss(100.0, 10.0)
        qemu.write_memory_bytes(self.mag_data_x_ptr, bytearray(struct.pack("=f", x)))
        qemu.write_memory_bytes(self.mag_data_y_ptr, bytearray(struct.pack("=f", y)))
        qemu.write_memory_bytes(self.mag_data_z_ptr, bytearray(struct.pack("=f", z)))
        logger.info(f"mag x/y/z: {x}/{y}/{z}")

        global lastmtime
        # Get argfile last modified time, but check the file is available first.
        mtime = lastmtime
        if isfile(argfile) and access(argfile, R_OK):
            mtime = getmtime(argfile)

        # If file has been modified, try to update arguments from it
        if mtime > lastmtime and self.load_arguments(qemu):
            lastmtime = mtime

#            # Taking special care with how motor_arm and alt_hold are written, since they are byte 
#            # booleans, and I'm not sure the system  is reacting properly.
#            # Note: When creating bytearray, it is always initializd to 0, so no need to set if
#            #       the desired value is Null.
#            #qemu.write_memory(self.motor_arm_ptr, 1, motor_arm)
#            #qemu.write_memory(self.alt_hold_ptr, 1, alt_hold)
#
#            # When disarming, reset the motor txf struct, so the motor readings fall back to zero
#            motor_arm_bytes = bytearray(1)
#            msp_txf_motor_ptr = 0x20000b6f
#            if motor_arm == 0:
#                msp_txf_motor_bytes = bytearray(8)
#                qemu.write_memory_bytes(msp_txf_motor_ptr, msp_txf_motor_bytes)
#            else:
#                motor_arm_bytes[0] = motor_arm
#            qemu.write_memory_bytes(self.motor_arm_ptr, motor_arm_bytes)
#
#            alt_hold_bytes = bytearray(1)
#            if alt_hold != 0:
#                alt_hold_bytes[0] = alt_hold
#            qemu.write_memory_bytes(self.alt_hold_ptr, alt_hold_bytes)
#
#            qemu.write_memory_bytes(self.throttle_ptr, bytearray(struct.pack("=f", throttle)))
#            qemu.write_memory_bytes(self.pitch_set_point_ptr, bytearray(struct.pack("=f", pitch_set_point)))
#            qemu.write_memory_bytes(self.roll_set_point_ptr, bytearray(struct.pack("=f", roll_set_point)))
#            qemu.write_memory_bytes(self.yaw_set_point_ptr, bytearray(struct.pack("=f", yaw_set_point)))

        return True, 0
