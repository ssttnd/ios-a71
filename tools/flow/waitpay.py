"""Wait for TWRP, write a package, then hand over to waitflash.py. No windows."""
import os, subprocess, sys, time
R = r"C:\Users\qqstee\Desktop\ios-a71"
SP = os.path.dirname(os.path.abspath(__file__))
ADB = r"C:\Users\qqstee\Desktop\misc\misc\71stuff\Minimal ADB and Fastboot\adb.exe"
NW = 0x08000000
PAY, SHA, TAG = sys.argv[1:4]
os.chdir(R)
for _ in range(360):
    if subprocess.run([ADB, "get-state"], capture_output=True, text=True, creationflags=NW).stdout.strip() == "recovery":
        break
    time.sleep(10)
else:
    sys.exit("phone never reached TWRP")
time.sleep(8)
p = subprocess.run([sys.executable, "-I", "-u", "-X", "utf8", r"tools\a71-payload.py", "write", ADB, PAY], capture_output=True, text=True, creationflags=NW)
print(p.stdout, p.stderr[-300:], flush=True)
if "VERIFY OK" not in p.stdout:
    sys.exit("package write failed")
sys.exit(subprocess.run([sys.executable, "-I", "-u", "-X", "utf8", os.path.join(SP, "waitflash.py"), SHA, TAG], creationflags=NW).returncode)
