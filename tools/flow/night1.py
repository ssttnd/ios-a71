"""Wait for the kernel build, for TWRP and a safe charge; then package with the live RTC offset, write and flash. No windows."""
import hashlib, json, os, re, subprocess, sys, time, urllib.request
SP = os.path.dirname(os.path.abspath(__file__))
R = r"C:\Users\qqstee\Desktop\ios-a71"
ADB = r"C:\Users\qqstee\Desktop\misc\misc\71stuff\Minimal ADB and Fastboot\adb.exe"
NW = 0x08000000
SHA, PAY, TAG, ARGS = sys.argv[1:5]
os.chdir(R)
def api(u):
    q = urllib.request.Request("https://api.github.com/repos/ssttnd/ios-a71" + u, headers={"User-Agent": "ios-a71-check"})
    return json.load(urllib.request.urlopen(q, timeout=60))
for _ in range(400):
    try:
        run = next((x for x in api(f"/actions/runs?head_sha={SHA}&per_page=6")["workflow_runs"] if x["name"] == "xnu"), None)
        if run and run["status"] == "completed":
            jobs = {j["name"]: j["conclusion"] for j in api(f"/actions/runs/{run['id']}/jobs")["jobs"]}
            print("xnu", run["id"], run["conclusion"], jobs, flush=True)
            if jobs.get("build") != "success":
                sys.exit("kernel build failed")
            if jobs.get("qemu-smoke") != "success":
                print("QEMU did not pass: check before flashing", flush=True)
                sys.exit(3)
            break
    except Exception as e:
        print("api", e, flush=True)
    time.sleep(30)
def sh(c):
    return subprocess.run([ADB, "shell", c], capture_output=True, timeout=60, creationflags=NW).stdout.decode(errors="replace").strip()
last = -1
while True:
    st = subprocess.run([ADB, "get-state"], capture_output=True, text=True, creationflags=NW).stdout.strip()
    if st == "recovery":
        cap = sh("cat /sys/class/power_supply/battery/capacity")
        if cap.isdigit() and int(cap) != last:
            last = int(cap)
            print(time.strftime("%H:%M:%S"), "charge", cap, flush=True)
        if cap.isdigit() and int(cap) >= 15:
            break
    time.sleep(60)
epoch = int(sh("cat /sys/class/rtc/rtc0/since_epoch"))
off = int(time.time()) - epoch
print("rtc", epoch, "offset", off, flush=True)
env = dict(os.environ, SWEETBATTERY="2", SWEETWLAN="1")
p = subprocess.run([sys.executable, "-X", "utf8", os.path.join(SP, "pk2.py"), SHA, f"{ARGS} sweetrtc=1 sweetrtcoff={off}"], capture_output=True, text=True, env=env, creationflags=NW)
print(p.stdout[-300:], p.stderr[-400:], flush=True)
if p.returncode:
    sys.exit("package failed")
img = hashlib.sha256(open(r"shim\out\shim-boot.img", "rb").read()).hexdigest()
sys.exit(subprocess.run([sys.executable, "-I", "-u", "-X", "utf8", os.path.join(SP, "waitpay.py"), PAY, img, TAG], creationflags=NW).returncode)
