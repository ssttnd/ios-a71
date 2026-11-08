#!/usr/bin/env python3
"""Repeatable UI load on the A71: injected swipes through sweetdbg, numbers from the running USB console log.

  python tools/a71-bench.py logs/usb-waveNN.txt [--host 192.168.0.192] [--seconds 42] [--tag name]

Swipes between two neighbouring pages (home and App Library, the heaviest blur on the phone). The phone must be unlocked,
on the last home page, and a71-usblog.py must be writing the log. Averages the 10 s kernel windows that lie fully inside
the run. Appends one line to logs/bench.txt (gitignored).
"""
import os, re, subprocess, sys, time

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
seconds = float(opt(args, "--seconds", "42"))
tag = opt(args, "--tag", "")
if len(args) != 1:
    sys.exit(__doc__)
log = args[0]


def swipe(x1, x2):
    subprocess.run([sys.executable, "-I", "-X", "utf8", os.path.join(ROOT, "tools", "a71-dbg.py"), "--host", host, "swipe",
                    str(x1), "1300", str(x2), "1300", "250"], capture_output=True, creationflags=NW)


start = os.path.getsize(log)
t0 = time.time()
n = 0
while time.time() - t0 < seconds:
    swipe(900, 150)
    time.sleep(0.75)
    swipe(150, 900)
    time.sleep(0.75)
    n += 2
time.sleep(1)
with open(log, "rb") as f:
    f.seek(start)
    text = f.read().decode("utf-8", "replace")

fb = re.findall(r"SWEET FB: timing window_ms=(\d+) callbacks=\d+ present_calls=(\d+) missed_deadlines=\d+ target=\d+ Hz "
                r"present_avg_us=(\d+) present_max_us=(\d+) coalesced=(\d+) swaps=(\d+) gate_avg_us=(\d+) acquire_avg_us=(\d+) copy_avg_us=(\d+)", text)
render = re.findall(r"SWEET RENDER: window_ms=\d+ gpu_busy_pct=(\d+)", text)
sched = re.findall(r"SWEET SCHED: window_ms=\d+ ui_run_us a55=(\d+) a76=(\d+)", text)
# the first and last windows straddle the run's edges
fb = fb[1:-1] if len(fb) > 2 else fb
render = render[1:-1] if len(render) > 2 else render
sched = sched[1:-1] if len(sched) > 2 else sched
if not fb:
    sys.exit("no complete timing window in the log: is a71-usblog.py writing " + log + "?")


def mean(vs):
    return sum(vs) / len(vs) if vs else 0.0


ms = mean([float(x[0]) for x in fb])
fps = mean([float(x[1]) for x in fb]) * 1000.0 / ms
res = {
    "presents/s": round(fps, 1),
    "swaps/s": round(mean([float(x[5]) for x in fb]) * 1000.0 / ms, 1),
    "present_avg_ms": round(mean([float(x[2]) for x in fb]) / 1000.0, 2),
    "present_max_ms": round(max(float(x[3]) for x in fb) / 1000.0, 1),
    "gate_ms": round(mean([float(x[6]) for x in fb]) / 1000.0, 2),
    "copy_ms": round(mean([float(x[8]) for x in fb]) / 1000.0, 2),
    "coalesced": round(mean([float(x[4]) for x in fb]), 1),
    "gpu_busy_%": round(mean([float(x) for x in render]), 1),
    "a55_s": round(mean([float(x[0]) for x in sched]) / 1e6, 2),
    "a76_s": round(mean([float(x[1]) for x in sched]) / 1e6, 2),
    "windows": len(fb),
    "swipes": n,
}
line = time.strftime("%H:%M:%S ") + (tag + " " if tag else "") + " ".join(f"{k}={v}" for k, v in res.items())
print(line)
with open(os.path.join(ROOT, "logs", "bench.txt"), "a", encoding="utf-8") as f:
    f.write(line + "\n")
