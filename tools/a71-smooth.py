#!/usr/bin/env python3
"""Frame pacing during motion on the A71: back-to-back injected swipes, then the real swap intervals
from the SWEET TAP lines that the running USB log (tools/a71-usblog.py) collects.

  python tools/a71-smooth.py logs/usb-waveNN.txt [--host 192.168.0.192] [--seconds 20] [--tag name]

tools/a71-bench.py's presents/s counts the idle gaps between its swipes, so it sits near 30 whatever
the GPU does. Here only intervals under 100 ms count (motion), and the result is presents/s over the
whole kernel timing windows inside the run (no idle gaps), plus TAP intervals when real touches open them. Appends one line to logs/smooth.txt.
"""
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NW = 0x08000000 if os.name == "nt" else 0


def opt(args, name, default):
    if name in args:
        i = args.index(name)
        v = args[i + 1]
        del args[i:i + 2]
        return v
    return default


args = sys.argv[1:]
host = opt(args, "--host", "192.168.0.192")
seconds = float(opt(args, "--seconds", "20"))
tag = opt(args, "--tag", "")
log = args[0]


def swipe(x1, x2):
    subprocess.run([sys.executable, "-I", "-X", "utf8", os.path.join(ROOT, "tools", "a71-dbg.py"), "--host", host,
                    "swipe", str(x1), "1300", str(x2), "1300", "250"], capture_output=True, creationflags=NW, timeout=30)


start = os.path.getsize(log)
t0 = time.time()
n = 0
while time.time() - t0 < seconds:
    swipe(900, 150)
    swipe(150, 900)
    n += 2
time.sleep(1.5)
with open(log, "rb") as f:
    f.seek(start)
    text = f.read().decode("utf-8", "replace")

fb = [(int(w), int(p), int(g)) for w, p, g in re.findall(
    r"SWEET FB: timing window_ms=(\d+) callbacks=\d+ present_calls=(\d+).*?gate_avg_us=(\d+)", text)]
fb = fb[1:-1] if len(fb) > 2 else fb
fps = 1000.0 * sum(p for _, p, _ in fb) / max(1, sum(w for w, _, _ in fb))
gate = sum(g for _, _, g in fb) / max(1, len(fb)) / 1000
iv = []
for m in re.finditer(r"swaps=\d+ at_ms=([\d,]+)", text):
    at = [int(x) for x in m.group(1).split(",")]
    iv += [b - a for a, b in zip(at, at[1:]) if 0 < b - a < 100]
if not fb:
    sys.exit("no complete SWEET FB timing window in the log: is a71-usblog.py writing " + log + "?")
iv.sort()
tap = (f" tap_intervals={len(iv)} on_time_%={100 * sum(1 for v in iv if v <= 18) / len(iv):.1f} "
       f"median_ms={iv[len(iv) // 2]}") if iv else ""
line = f"{time.strftime('%H:%M:%S')} {tag} fps={fps:.1f} gate_ms={gate:.1f} windows={len(fb)} swipes={n}{tap}"
print(line)
with open(os.path.join(ROOT, "logs", "smooth.txt"), "a") as f:
    f.write(line + "\n")
