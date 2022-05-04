from glob import glob
import pyrebase
import time
import RPi.GPIO as GPIO
from RpiMotorLib import RpiMotorLib
import drivers
import os
import glob
from datetime import datetime

#Parameters
MAX_MUG_DIST = 8.00         #Max accepted ultrasonic sensor reading, in cm
ULTRA_TIMEOUT_LIM = 1000
HEAT_TIMEOUT_LIM
GROUNDS_CONST

PUMP_DURATION = {
    8 : 7.00,
    12 : 10.50,
    16 : 14.50,
    20 : 17.50,
    24 : 21.00
}

VALVE_DURATION = {
    8 : 30.00,
    12 : 45.00,
    16 : 60.00,
    20 : 75.00,
    24 : 90.00
}

STRENGTH_CURVE = {
    1 : 0.6,
    2 : 0.7,
    3 : 0.8,
    4 : 0.9,
    5 : 1.0
}

#GPIO PINS
ULTRA_TRIG = 23
ULTRA_ECHO = 24

MTR_STEP = 13
MTR_DIR = 19
MTR_SLEEP = 26

PUMP = 21
HEATER = 6
VALVE = 13

#Firebase Initialization
config = {
    "apiKey": "",
    "authDomain": "",
    "databaseURL": "",
    "storageBucket": ""
}

firebase = pyrebase.initialize_app(config)
storage = firebase.storage()
db = firebase.database()


# Device Initialization

os.system('modprobe w1-gpio')
os.system('modprobe w1-therm')
base_dir = '/sys/bus/w1/devices/'
device_folder = glob.glob(base_dir + '28*')[0]
device_file = device_folder + '/w1_slave'

display = drivers.Lcd()

stepper = RpiMotorLib.A4988Nema(MTR_DIR, MTR_STEP, (-1, -1, -1), "A4988")

GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

GPIO.setup(ULTRA_TRIG,GPIO.OUT)
GPIO.setup(ULTRA_ECHO,GPIO.IN)

GPIO.setup(MTR_STEP,GPIO.OUT)
GPIO.setup(MTR_DIR,GPIO.OUT)
GPIO.setup(MTR_SLEEP,GPIO.OUT)

GPIO.setup(PUMP,GPIO.OUT)
GPIO.setup(HEATER,GPIO.OUT)
GPIO.setup(VALVE,GPIO.OUT)


# Sets GPIO outputs to default signals to return machine to a safe state
# To be used when an error occurs but the program does not need to exit
# If the program is about to exit, use GPIO.cleanup() instead
def resetGPIO():
	print("Resetting GPIO")
	
	GPIO.output(ULTRA_TRIG, False)
	
	GPIO.output(MTR_STEP, False)
	GPIO.output(MTR_DIR, False)
	GPIO.output(MTR_SLEEP, False)
	
	GPIO.output(PUMP,True)
	GPIO.output(VALVE,True)
	GPIO.output(HEATER,True)


# Reads from the temperature sensor 1-Wire device file
def read_temp_raw():
    f = open(device_file, 'r')
    lines = f.readlines()
    f.close()
    return lines


# Processes raw temperature sensor data from device file
# Returns the most recently measured temperature in degrees Fahrenheit
def read_temp():
    lines = read_temp_raw()
    while lines[0].strip()[-3:] != 'YES':
        time.sleep(0.2)
        lines = read_temp_raw()
    equals_pos = lines[1].find('t=')
    if equals_pos != -1:
        temp_string = lines[1][equals_pos+2:]
        temp_c = float(temp_string) / 1000.0
        temp_f = temp_c * 9.0 / 5.0 + 32.0
        return temp_f


# Clears only the specified line on the LCD
def clear_line(line):
	display.lcd_display_string("                    ", line) 
	

# Runs in the database listener thread when the brew state changes to "Begin"
def stream_handler(message):
	
	print('event={m[event]};path={m[path]};data={m[data]}'
		.format(m=message))
	if (message["data"] == 1):
		userSize = db.child("Brew").child("Size").get().val()
		userStrength = db.child("Brew").child("Strength").get().val()
		userTemp = db.child("Brew").child("Temperature").get().val()
		time.sleep(2)
		db.child("Brew").update({"Begin": 0})
		
		# calculate volume-adjusted grounds amount
		groundsAmount = GROUNDS_CONST * STRENGTH_CURVE[userStrength] * userSize
		
		brew(userSize, groundsAmount, userTemp)
		
		
# Main brewing process
# Returns True if brew completes with no errors,
# False if error interrupts brew
def brew(volume, strength, temp):
	display.lcd_display_string("Brew request", 1)
	display.lcd_display_string("received!", 2)
	time.sleep(2)
	display.lcd_clear()
	display.lcd_display_string("NOW BREWING", 1)
	time.sleep(1)
	
	if (mug_check() == False):
		return False
		
	pump_water(volume)
	
	if (heat_water(temp) == False):
		return False
		
	pour_grounds(0)
	
	pour_coffee(volume)
	
	return True
	
	
# Checks if mug is in place (within MAX_MUG_DIST cm)
# Returns True if mug is detected,
# False if mug is not detected or sensor times out	
def mug_check():
	
	clear_line(2)
	clear_line(3)
	clear_line(4)
	display.lcd_display_string("Detecting mug...", 2)
	print("Detecting mug...")
	time.sleep(2)
	

	# Send TRIG pulse
	GPIO.output(ULTRA_TRIG, True)
	time.sleep(0.00001)
	GPIO.output(ULTRA_TRIG, False)
	

	# Measure length of ECHO pulse and calculate distance
	ultraTimeout = 0
	while GPIO.input (ULTRA_ECHO)==0:
		pulse_start = time.time()
		
		# If ECHO stays low too long, sensor has likely failed
		ultraTimeout += 1
		if ultraTimeout == ULTRA_TIMEOUT_LIM:
			clear_line(2)
			clear_line(3)
			clear_line(4)
			display.lcd_display_string("Ultrasonic failure!", 2) 
			display.lcd_display_string("Please check", 3)
			display.lcd_display_string("sensor connections.", 4)
			print("Ultrasonic failure! Please check sensor connections.")
			return False
			
	while GPIO.input (ULTRA_ECHO)==1:
		pulse_end = time.time()
	
	pulse_duration = pulse_end - pulse_start
	distance = pulse_duration * 17150
	distance = round(distance,2)
	print("Distance: ", distance, "cm")
	
	
	# Compare measured distance to acceptable limit
	if (distance > MAX_MUG_DIST):
		clear_line(2)
		display.lcd_display_string("No mug detected!", 2) 
		display.lcd_display_string("Please insert mug", 3)
		display.lcd_display_string("and try again.", 4)  
		print("No mug detected! Please insert mug and try again")
		return False
	else:
		clear_line(2)
		display.lcd_display_string("Mug detected!", 2) 
		print("Mug detected!")
		time.sleep(2)
		return True


# Pumps specified volume of water into heating reservoir
def pump_water(volume):
	
	clear_line(2)
	clear_line(3)
	clear_line(4)
	display.lcd_display_string("Pumping water...", 2)
	display.lcd_display_string("Size: %doz" % volume, 3)
	print("Pumping water...")
	print("Size: %doz" % volume)
	time.sleep(1)
	
	# Switch on the pump relay for the duration corresponding to the volume
	GPIO.output(PUMP, False)
	time.sleep(PUMP_DURATION[volume])
	GPIO.output(PUMP, True)
	time.sleep(1)
	
	clear_line(2)
	clear_line(3)
	display.lcd_display_string("Pumping complete!", 2)
	print("Pumping complete!")
	time.sleep(2)
	
	
# Heats water in the heating reservoir until specified temp is reached
# Returns true if stage completes successfully,
# False if sensor cannot be read or temperature increase times out
def heat_water(temp):
	
	clear_line(2)
	clear_line(3)
	clear_line(4)
	display.lcd_display_string("Heating water...", 2)
	display.lcd_display_string("Current temp:" , 3) 
	display.lcd_display_string("Target temp:  %d" % temp, 4)
	time.sleep(1)
	
	# Switch on the heating element relay
	GPIO.output(HEATER,False)
	 
	# Heating loop
	currentTemp = 0
	lastTemp = 0
	heatTimeout = 0
	while(currentTemp < temp):
		
		# Handle exceptions caused by disconnected sensor
		try:
			currentTemp = read_temp();
		except (FileNotFoundError, IndexError) as e:
			clear_line(2)
			clear_line(3)
			clear_line(4)
			display.lcd_display_string("Temp Sensor Failure!", 2) 
			display.lcd_display_string("Please check", 3)
			display.lcd_display_string("sensor connections.", 4)
			print("Temp Sensor Failure! Please check sensor connections.")
			return False
			
		# Increment heating timeout counter if temp did not increase
		if currentTemp <= lastTemp:
			heatTimeout += 1
		else:
			heatTimeout = 0
			lastTemp = currentTemp
		
		# If temp has not increased for too long, something has likely failed
		if heatTimeout == HEAT_TIMEOUT_LIM:
			clear_line(2)
			clear_line(3)
			clear_line(4)
			display.lcd_display_string("Heating Failure!", 2) 
			display.lcd_display_string("Please check", 3)
			display.lcd_display_string("heater and pump.", 4)
			print("Heating Failure! Please check heater and pump.")
			return False
				
		display.lcd_display_string("Current temp: %d " % currentTemp, 3) 
		print("Current Temperature: %d" % currentTemp)
	
	# Switch off the heating element relay
	GPIO.output(HEATER,True)
	time.sleep(1)
	
	clear_line(2)
	clear_line(3)
	clear_line(4)
	display.lcd_display_string("Heating complete!", 2)
	print("Heating complete!")
	time.sleep(2)
	return True


# Pours the specified amount of grounds into the filter
def pour_grounds(amount):
	
	clear_line(2)
	clear_line(3)
	clear_line(4)
	display.lcd_display_string("Pouring grounds...", 2)
	print("Pouring grounds...")
	
	# Set the stepper motor to turn based on the specified amount of grounds
	GPIO.output(MTR_SLEEP, True)
	stepper.motor_go(True, "Full", amount*200, .05, False, .05)
	GPIO.output(MTR_SLEEP, False)
				
	clear_line(2)
	display.lcd_display_string("Grounds poured!", 2)
	print("Grounds poured!")
	time.sleep(2)
	
	return


# Pours heated water over grounds in filter to brew the coffee
def pour_coffee(volume):
	
	clear_line(2)
	clear_line(3)
	clear_line(4)
	display.lcd_display_string("Pouring coffee...", 2)
	display.lcd_display_string("Please wait until", 3)
	display.lcd_display_string("fully poured.", 4)
	print("Pouring coffee... Please wait until fully poured.")
	time.sleep(1)
	
	# Switch on the valve relay for the duration corresponding to the volume
	GPIO.output(VALVE, False)
	time.sleep(VALVE_DURATION[volume])
	GPIO.output(VALVE, True)
	time.sleep(1)
	
	clear_line(2)
	clear_line(3)
	clear_line(4)
	display.lcd_display_string("Pouring complete!", 2)
	print("Pouring complete!")
	time.sleep(2)



display.lcd_clear()
resetGPIO()

my_stream = db.child("Brew").child("Begin").stream(stream_handler)


print("Displaying clock")
display.lcd_display_string("   The Smart Drip   ", 2)


# Test without app below:
clockBreak = 0
while clockBreak < 100:
    display.lcd_display_string("      " + datetime.now().strftime("%H:%M:%S") + "      ", 3)
    clockBreak += 1

display.lcd_clear()
if brew(20, 1, 70) == True:
	display.lcd_clear()
	display.lcd_display_string("BREW COMPLETE", 1)
	display.lcd_display_string("Enjoy!", 2)
else:
	clear_line(1)
	display.lcd_display_string("BREWING ERROR", 1)
	resetGPIO()