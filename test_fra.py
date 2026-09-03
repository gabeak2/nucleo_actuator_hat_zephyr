import sys
import serial
import time

port = '/dev/cu.usbmodem114403'
baud = 921600

ser = serial.Serial(port, baud, timeout=0.5)
ser.write(b'\r\n\r\n')
time.sleep(0.1)
ser.read_all()

cmd = "dac test fra 0 100 10 100 10000 10\r\n"
ser.write(cmd.encode())

start = time.time()
while time.time() - start < 60:
    chunk = ser.read(1)
    if chunk:
        sys.stdout.buffer.write(chunk)
        sys.stdout.flush()
        if b"uart:~$" in chunk:
            break
