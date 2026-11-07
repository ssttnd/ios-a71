"""Download the bundle of a commit, verify it, build the package. No phone access."""
import hashlib, json, os, shutil, subprocess, sys, zipfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ghauth
SHA = sys.argv[1]; TAG = SHA[:7]
R = r"C:\Users\qqstee\Desktop\ios-a71"
NW = 0x08000000
os.chdir(R)
runs = ghauth.get(f"/actions/runs?head_sha={SHA}&per_page=5")["workflow_runs"]
for r in runs:
    print("run", r["name"], r["id"], r["status"], r["conclusion"])
r = next(x for x in runs if x["name"] == "sweet-native-metal")
assert r["conclusion"] == "success"
a = next(x for x in ghauth.get(f"/actions/runs/{r['id']}/artifacts")["artifacts"] if x["name"] == "sweet-native-metal-ios16")
d = ghauth.get(f"/actions/artifacts/{a['id']}/zip", binary=True)
mz = os.path.join("research", f"native-metal-{TAG}.zip")
open(mz, "wb").write(d)
dst = os.path.join("research", f"native-metal-{TAG}")
shutil.rmtree(dst, ignore_errors=True)
zipfile.ZipFile(mz).extractall(dst)
b = os.path.join(dst, "native-legacy", "SweetMetal2.bundle")
info = json.load(open(os.path.join(dst, "native-legacy", "build.json")))
assert info["source_commit"] == SHA, info["source_commit"]
for rel, h in info["files"].items():
    assert hashlib.sha256(open(os.path.join(b, *rel.split("/")), "rb").read()).hexdigest() == h, rel
be = open(os.path.join(b, "libSweetNativeBackend.dylib"), "rb").read()
fe = open(os.path.join(b, "SweetMetal2"), "rb").read()
print("marker backend", b"rendering into mip level" in be, "frontend", b"lod clamp" in fe, len(info["files"]))
pay = os.path.join("build", f"metal-{TAG}.payload")
q = subprocess.run([sys.executable, "-I", "-X", "utf8", r"tools\a71-payload.py", "build", pay,
    f"tree:{b}=/System/Library/Extensions/SweetMetal2.bundle"], capture_output=True, text=True, creationflags=NW)
print(q.stdout[-420:], q.stderr[-300:])
assert q.returncode == 0
