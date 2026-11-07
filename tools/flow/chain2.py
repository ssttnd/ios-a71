"""Wait for the bundle, package it with the LaunchServices job, write and flash. No windows."""
import json, os, shutil, subprocess, sys, time, urllib.request
SP = os.path.dirname(os.path.abspath(__file__))
R = r"C:\Users\qqstee\Desktop\ios-a71"
SHA, IMG, TAG = sys.argv[1:4]
NW = 0x08000000
os.chdir(R)
def runs():
    q = urllib.request.Request(f"https://api.github.com/repos/ssttnd/ios-a71/actions/runs?head_sha={SHA}&per_page=5", headers={"User-Agent": "ios-a71-check"})
    return json.load(urllib.request.urlopen(q, timeout=60))["workflow_runs"]
for _ in range(120):
    try:
        r = [x for x in runs() if x["name"] == "sweet-native-metal"]
        if r and r[0]["status"] == "completed":
            print("ci", r[0]["id"], r[0]["conclusion"], flush=True)
            if r[0]["conclusion"] != "success":
                sys.exit("bundle build failed")
            break
    except Exception as e:
        print("api", e, flush=True)
    time.sleep(20)
else:
    sys.exit("timeout")
p = subprocess.run([sys.executable, "-X", "utf8", os.path.join(SP, "pm.py"), SHA], capture_output=True, text=True, creationflags=NW)
print(p.stdout[-300:], p.stderr[-400:], flush=True)
if p.returncode:
    sys.exit("bundle check failed")
tree = r"build\ls\tree"
shutil.rmtree(tree, ignore_errors=True)
os.makedirs(os.path.join(tree, "System", "Library", "xpc"))
os.makedirs(os.path.join(tree, "usr", "local", "libexec"))
shutil.copy2(r"build\ls\launchd.plist", os.path.join(tree, "System", "Library", "xpc", "launchd.plist"))
shutil.copy2(r"build\ls\sweet-wallpaper-register", os.path.join(tree, "usr", "local", "libexec", "sweet-wallpaper-register"))
b = os.path.join("research", f"native-metal-{SHA[:7]}", "native-legacy", "SweetMetal2.bundle")
pay = rf"build\all-{SHA[:7]}.payload"
q = subprocess.run([sys.executable, "-I", "-X", "utf8", r"tools\a71-payload.py", "build", pay,
    f"tree:{b}=/System/Library/Extensions/SweetMetal2.bundle", f"tree:{tree}="], capture_output=True, text=True, creationflags=NW)
print(q.stdout[-700:], q.stderr[-300:], flush=True)
if q.returncode:
    sys.exit("package failed")
sys.exit(subprocess.run([sys.executable, "-I", "-u", "-X", "utf8", os.path.join(SP, "waitpay.py"), pay, IMG, TAG], creationflags=NW).returncode)
