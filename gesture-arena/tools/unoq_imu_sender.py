"""
Runs on the Arduino UNO Q (Linux side / App Lab Python).

Reads a 6-axis IMU (e.g. Modulino Movement / LSM6DSOX on the Qwiic
connector), estimates aim direction (yaw/pitch) and swing force, and
streams JSON over UDP to the laptop game server.

The UNO Q has NO built-in IMU - attach one via Qwiic. Default I2C
address for LSM6DSOX is 0x6A.

Setup on the UNO Q:
    pip install smbus2

Test WITHOUT hardware (from any machine, even the laptop itself):
    python unoq_imu_sender.py --sim

Packet format (50 Hz):
    {"yaw": deg, "pitch": deg, "force": 0..1, "event": "swing" | null}
"""

import json
import math
import socket
import sys
import time

LAPTOP_IP = "192.168.1.100"   # <-- EDIT: your laptop's LAN IP (game server prints it)
UDP_PORT = 5005

SWING_THRESHOLD_G = 1.2       # accel beyond gravity that counts as a swing
SWING_COOLDOWN_S = 0.6
SEND_HZ = 50

SIM = "--sim" in sys.argv

# ---------------- IMU driver (LSM6DSOX over I2C) ----------------
if not SIM:
    from smbus2 import SMBus
    ADDR = 0x6A
    bus = SMBus(1)                      # adjust bus number if needed
    bus.write_byte_data(ADDR, 0x10, 0x40)  # CTRL1_XL: accel 104 Hz, +/-2 g
    bus.write_byte_data(ADDR, 0x11, 0x40)  # CTRL2_G:  gyro  104 Hz, 250 dps

    def read_imu():
        """Returns (ax, ay, az) in g and (gx, gy, gz) in deg/s."""
        raw = bus.read_i2c_block_data(ADDR, 0x22, 12)  # gyro then accel
        vals = []
        for i in range(0, 12, 2):
            v = raw[i] | (raw[i + 1] << 8)
            vals.append(v - 65536 if v > 32767 else v)
        gx, gy, gz, ax, ay, az = vals
        s_a, s_g = 0.000061, 0.00875   # 2 g and 250 dps scale factors
        return (ax * s_a, ay * s_a, az * s_a), (gx * s_g, gy * s_g, gz * s_g)
else:
    def read_imu():
        """Fake gentle motion + a swing every ~3 s, for testing the pipeline."""
        t = time.time()
        ax = 0.3 * math.sin(t * 0.7)
        az = 1.0 + (2.5 if (t % 3.0) < 0.08 else 0.0)   # periodic 'swing' spike
        return (ax, 0.05 * math.sin(t), az), (0.0, 0.0, 25.0 * math.sin(t * 0.5))

# ---------------- Main loop ----------------
def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    yaw, pitch = 0.0, 10.0
    last_swing, last_send = 0.0, 0.0
    prev = time.time()
    print(f"Streaming to {LAPTOP_IP}:{UDP_PORT}  (sim={SIM})")

    while True:
        (ax, ay, az), (gx, gy, gz) = read_imu()
        now = time.time()
        dt = min(now - prev, 0.05)
        prev = now

        # Pitch: complementary filter (gyro integration + accel gravity ref)
        pitch_acc = math.degrees(math.atan2(-ax, math.hypot(ay, az)))
        pitch = 0.98 * (pitch + gy * dt) + 0.02 * pitch_acc

        # Yaw: gyro integration with a slow leak back to center
        # (no magnetometer, so absolute yaw drifts; leak keeps aim usable)
        yaw = (yaw + gz * dt) * 0.999

        # Swing detection: accel magnitude spike beyond gravity
        mag = abs(math.sqrt(ax * ax + ay * ay + az * az) - 1.0)
        event, force = None, 0.0
        if mag > SWING_THRESHOLD_G and now - last_swing > SWING_COOLDOWN_S:
            last_swing = now
            force = min(1.0, 0.3 + (mag - SWING_THRESHOLD_G) / 3.0)
            event = "swing"
            print(f"swing! force={force:.2f}")

        # Send at SEND_HZ (swings always sent immediately)
        if event or now - last_send >= 1.0 / SEND_HZ:
            last_send = now
            pkt = {"yaw": round(yaw, 2), "pitch": round(pitch, 2),
                   "force": round(force, 3), "event": event}
            sock.sendto(json.dumps(pkt).encode(), (LAPTOP_IP, UDP_PORT))

        time.sleep(0.005)

if __name__ == "__main__":
    main()
