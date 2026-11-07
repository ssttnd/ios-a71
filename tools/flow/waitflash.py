"""Wait until the phone is in TWRP, flash the given packaged image (owner approved test images), watch the USB log. No windows."""
import hashlib, os, subprocess, sys, time
R = r"C:\Users\qqstee\Desktop\ios-a71"
ADB = r"C:\Users\qqstee\Desktop\misc\misc\71stuff\Minimal ADB and Fastboot\adb.exe"
EXPECT, TAG = sys.argv[1], sys.argv[2]
NW = 0x08000000
os.chdir(R)
assert hashlib.sha256(open(r"shim\out\shim-boot.img", "rb").read()).hexdigest() == EXPECT
subprocess.run(["powershell", "-NoProfile", "-Command", "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'a71-usblog|a71-bootcheck' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"], creationflags=NW)
for _ in range(360):
    if subprocess.run([ADB, "get-state"], capture_output=True, text=True, creationflags=NW).stdout.strip() == "recovery":
        break
    time.sleep(10)
else:
    sys.exit("phone never reached TWRP")
time.sleep(8)
f = subprocess.run([sys.executable, "-I", "-u", "-X", "utf8", r"tools\a71-flash-boot.py", ADB, r"shim\out\shim-boot.img", EXPECT], capture_output=True, text=True, creationflags=NW)
print(f.stdout[-300:], f.stderr[-300:], flush=True)
if "VERIFY OK" not in f.stdout:
    sys.exit("flash failed")
log = os.path.join("logs", f"usb-{TAG}.txt")
open(log, "ab").close()
subprocess.Popen([sys.executable, "-I", "-u", "-X", "utf8", r"tools\a71-usblog.py", log], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NW)
c = subprocess.run([sys.executable, "-I", "-u", "-X", "utf8", r"tools\a71-bootcheck.py", log, "240"], capture_output=True, text=True, creationflags=NW)
print(c.stdout[-1200:], flush=True)
time.sleep(90)
t = open(log, encoding="utf-8", errors="replace").read().split("\n")
print("\n".join(l[:200] for l in t if "SWEET ADRENO" in l or "panic(" in l or "CP firmware" in l)[-6000:], flush=True)
