#!/usr/bin/env python3
"""After a reboot, report whether iOS came up, from the USB console log. Windows only.

  a71-bootcheck.py LOG.txt [SECONDS]

Needs tools/a71-usblog.py writing LOG.txt and a boot image with sweetusb=3. Prints
each milestone with its time and exits with a verdict. Read-only: it sends nothing
to the phone. No USB port by the end means the kernel did not reach the USB step (8 s).
"""
import os, re, subprocess, sys, time

LOG = sys.argv[1]
LIMIT = int(sys.argv[2]) if len(sys.argv) > 2 else 300
STEPS = [("kernel", "Darwin Kernel Version"), ("USB up", "SWEET USB: running"), ("root mounted", "hfs: mounted"),
         ("launchd", "exec launchd"), ("backboardd", "exec backboardd"), ("SpringBoard", "exec SpringBoard"),
         ("first frames", "SWEET FB: 10 s: presents")]
t0 = time.time()
start_size = os.path.getsize(LOG) if os.path.exists(LOG) else 0
seen, port = set(), False


def has_port():
    ps = "Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -match 'VID_1209&PID_0002' } | Select-Object -First 1 -ExpandProperty Status"
    return "OK" in subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True).stdout


while time.time() - t0 < LIMIT:
    if not port and has_port():
        port = True
        print(f"{time.time() - t0:5.0f}s  USB port present", flush=True)
    data = b""
    if os.path.exists(LOG):
        with open(LOG, "rb") as f:
            f.seek(start_size)
            data = f.read()
    text = data.decode("utf-8", "replace")
    for name, needle in STEPS:
        if name not in seen and needle in text:
            seen.add(name)
            print(f"{time.time() - t0:5.0f}s  {name}", flush=True)
    panic = re.search(r"panic\(cpu[^\n]*", text)
    if panic:
        print("PANIC:", panic.group(0)[:300], flush=True)
        break
    if "first frames" in seen and text.count("SWEET FB: 10 s: presents") >= 2:
        break
    time.sleep(5)
crashes = re.findall(r"^CRASH (\S+?)\[\d+\] sig", text, re.M)
print("crashes:", {c: crashes.count(c) for c in sorted(set(crashes))})
for k in ["SWEET RTC", "SWEET CPUFREQ: domain 1 selected"]:
    print("\n".join(l[:150] for l in text.split("\n") if k in l)[:400])
print("VERDICT:", "UP" if "first frames" in seen else ("kernel running, UI not up" if port else "no USB: boot failed before 8 s or USB failed"), flush=True)
