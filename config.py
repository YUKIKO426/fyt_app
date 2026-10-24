# config.py — MSI Thin B12UCX tuned

import subprocess
import re

API_ID       = 0
API_HASH     = ""
SESSION_NAME = "fyt_dominant"

HTTP_HOST = "127.0.0.1"
HTTP_PORT = 8765

SAMPLE_RATE = 48000
CHUNK       = 960
CHANNELS    = 1


def detect_input_device():
    try:
        out = subprocess.check_output(
            ["arecord", "-l"], stderr=subprocess.DEVNULL, text=True
        )
        for line in out.splitlines():
            if "card" in line and "device" in line and "capture" in line.lower():
                m = re.search(r"card (\d+):.*device (\d+):", line)
                if m:
                    return f"hw:{m.group(1)},{m.group(2)}"
    except Exception:
        pass
    return None


INPUT_DEVICE = detect_input_device()
print(f"[config] mic device: {INPUT_DEVICE or 'default'}")
