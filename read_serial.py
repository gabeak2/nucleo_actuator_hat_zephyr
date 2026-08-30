import serial
import time
port = '/dev/cu.usbmodem114403'
baud = 115200
ser = serial.Serial(port, baud, timeout=1)
ser.write(b'\r\n\r\n')
start = time.time()
while time.time() - start < 5:
    chunk = ser.read(1024)
    if chunk:
        print(chunk.decode(errors='replace'), end='', flush=True)
