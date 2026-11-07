"""Build the combined package for the next flash: Wi-Fi, apps, time zone, launchd jobs, network service, bundle. No phone access."""
import hashlib, importlib.util, io, os, plistlib, shutil, subprocess, sys, zipfile
SP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SP)
import ghauth
R = r"C:\Users\qqstee\Desktop\ios-a71"
NW = 0x08000000
os.chdir(R)
WIFI_RUN, METAL_SHA = sys.argv[1], sys.argv[2]
ST = r"build\next"
shutil.rmtree(ST, ignore_errors=True)
def put(rel, data):
    p = os.path.join(ST, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "wb").write(data)
a = next(x for x in ghauth.get(f"/actions/runs/{WIFI_RUN}/artifacts")["artifacts"] if x["name"] == "sweet-wifi-ios16")
z = zipfile.ZipFile(io.BytesIO(ghauth.get(f"/actions/artifacts/{a['id']}/zip", binary=True)))
for n in ("wland", "netsvc"):
    put(f"private/var/local/bin/{n}", z.read(n))
    print(n, len(z.read(n)), hashlib.sha256(z.read(n)).hexdigest()[:12])
fw = r"research\a71-firmware"
for n in sorted(os.listdir(os.path.join(fw, "modem"))):
    if n.startswith("modem") :
        put(f"private/var/local/fw/modem/{n}", open(os.path.join(fw, "modem", n), "rb").read())
for n in sorted(os.listdir(os.path.join(fw, "apnhlos"))):
    if n.startswith(("bdwlan", "wlanmdsp", "regdb", "modemuw")):
        put(f"private/var/local/fw/modem/{n}", open(os.path.join(fw, "apnhlos", n), "rb").read())
put("private/var/local/fw/rw/.keep", b"")
for n in ("modemst1", "modemst2", "fsg", "fsc"):
    put(f"private/var/local/fw/rmtfs/{n}.img", open(os.path.join("backup", "2026-11-06", n + ".img"), "rb").read())
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("hfsread", r"tools\hfsread.py")
h = importlib.util.module_from_spec(spec); spec.loader.exec_module(h)
f = h.HFS(r"backup\phone-root-now.img")
# launchd: the file on the phone already has the rebuild job
cur = f.readfile("/System/Library/xpc/launchd.plist")
d = plistlib.loads(cur)
assert "/System/Library/LaunchDaemons/org.sweet.wallpaper-rebuild.plist" in d["LaunchDaemons"]
k = "/System/Library/LaunchDaemons/org.sweet.wland.plist"
d["LaunchDaemons"][k] = {"Label": "org.sweet.wland", "ProgramArguments": ["/var/local/bin/wland", "auto"], "RunAtLoad": True, "UserName": "root",
    "EnvironmentVariables": {"PATH": "/var/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"}}
put("System/Library/xpc/launchd.plist", plistlib.dumps(d, fmt=plistlib.FMT_BINARY, sort_keys=False))
print("jobs", len(d["LaunchDaemons"]))
# network service, as the donor's netprefs
PREFS = "/private/var/preferences/SystemConfiguration/preferences.plist"
raw = f.readfile(PREFS)
p = plistlib.loads(raw)
open(r"build\ls\preferences.plist.orig", "wb").write(raw)
print("prefs keys", list(p))
SVC = "5EE7C0DE-5EE7-4000-8000-00000000E000"
SET = "5EE7C0DE-5EE7-4000-8000-00000000A000"
p.setdefault("NetworkServices", {})[SVC] = {"DNS": {}, "IPv4": {"ConfigMethod": "DHCP"}, "IPv6": {"ConfigMethod": "Automatic"},
    "Proxies": {"ExceptionsList": ["*.local", "169.254/16"], "FTPPassive": 1},
    "Interface": {"DeviceName": "en0", "Hardware": "AirPort", "Type": "Ethernet", "UserDefinedName": "Wi-Fi"}, "UserDefinedName": "Wi-Fi"}
p.setdefault("Sets", {})[SET] = {"Network": {"Global": {"IPv4": {"ServiceOrder": [SVC]}},
    "Interface": {"en0": {"AirPort": {"JoinModeFallback": ["DoNothing"], "PowerEnabled": True}}},
    "Service": {SVC: {"__LINK__": "/NetworkServices/" + SVC}}}, "UserDefinedName": "Automatic"}
p["CurrentSet"] = "/Sets/" + SET
put("private/var/preferences/SystemConfiguration/preferences.plist", plistlib.dumps(p, fmt=plistlib.FMT_BINARY))
# bundle
q = subprocess.run([sys.executable, "-X", "utf8", os.path.join(SP, "pm.py"), METAL_SHA], capture_output=True, text=True, creationflags=NW)
print(q.stdout[-200:], q.stderr[-300:])
assert q.returncode == 0
b = os.path.join("research", f"native-metal-{METAL_SHA[:7]}", "native-legacy", "SweetMetal2.bundle")
staged = ['AppleTV', 'Books', 'Bridge', 'Calculator', 'Compass', 'Contacts', 'FaceTime', 'Files', 'FindMy', 'Fitness', 'Freeform', 'Health', 'Home', 'Magnifier', 'Maps', 'Measure', 'MobileCal', 'MobileMail', 'MobileNotes', 'MobileStore', 'MobileTimer', 'Music', 'News', 'Passbook', 'Podcasts', 'Reminders', 'SequoiaTranslator', 'Shortcuts', 'Stocks', 'Tips', 'VoiceMemos', 'Weather']
items = [f"tree:{b}=/System/Library/Extensions/SweetMetal2.bundle", f"tree:{ST}=", "link:/private/var/db/timezone/localtime=/usr/share/zoneinfo.default/Asia/Qyzylorda"]
items += [f"mv:/private/var/staged_system_apps/{n}.app=/Applications/{n}.app" for n in staged]
pay = r"build\next.payload"
q = subprocess.run([sys.executable, "-I", "-X", "utf8", r"tools\a71-payload.py", "build", pay] + items, capture_output=True, text=True, creationflags=NW)
print(q.stdout[-400:], q.stderr[-400:])
assert q.returncode == 0
