"""Download a run's kernel and package an image: pk2.py SHA "EXTRA ARGS". No phone access."""
import hashlib, os, subprocess, sys, zipfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ghauth
SHA, ARGS = sys.argv[1], sys.argv[2]
R = r"C:\Users\qqstee\Desktop\ios-a71"
NW = 0x08000000
os.chdir(R)
kz = os.path.join("research", f"xnu-kernel-{SHA[:7]}.zip")
if not os.path.exists(kz):
    run = next(r for r in ghauth.get(f"/actions/runs?head_sha={SHA}&per_page=5")["workflow_runs"] if r["name"] == "xnu")
    print("run", run["id"], run["status"], run["conclusion"])
    a = next(x for x in ghauth.get(f"/actions/runs/{run['id']}/artifacts")["artifacts"] if x["name"] == "xnu-kernel")
    d = ghauth.get(f"/actions/artifacts/{a['id']}/zip", binary=True)
    assert len(d) == a["size_in_bytes"]
    open(kz, "wb").write(d)
k = zipfile.ZipFile(kz).read("kernel.development.sweet")
print("kernel", len(k), hashlib.sha256(k).hexdigest()[:16], b"SWEET DRAWLOG: armed" in k, b"SWEET PAYLOAD: finished" in k)
if os.path.exists(r"shim\out\shim-boot.img"):
    os.remove(r"shim\out\shim-boot.img")
args = "ufslogsecs=1 sweetgpukhz=650000 sweetmtl=2 sweetgles=0 sweetpresentwait=1 sweetpresentlock=1 " + ARGS
env = dict(os.environ, SWEETGPU=os.environ.get("SWEETGPU", "9"), SWEETNULL=os.environ.get("SWEETNULL", "backboardd"), SWEETARGS=args.strip())
q = subprocess.run([sys.executable, "-X", "utf8", r"tools\package_gpu_boot.py", kz], capture_output=True, text=True, env=env, creationflags=NW)
print(q.stderr[-200:])
assert q.returncode == 0
img = open(r"shim\out\shim-boot.img", "rb").read()
for s in args.split():
    assert s.encode() in img, s
assert b"sRGB" in img and b"mac-address-wifi0" in img and k[:65536] in img
print("image", hashlib.sha256(img).hexdigest())
