#!/usr/bin/env python3
"""Install files into the A71's iOS root without a network (kernel patch 0191).

  a71-payload.py build OUT.bin ITEM...      make a package and print its SHA-256
  a71-payload.py write ADB_EXE OUT.bin      write it from TWRP and verify by readback
  a71-payload.py clear ADB_EXE              remove a package that was not applied

ITEM is one of:
  tree:LOCAL_DIR=/dest       every file below LOCAL_DIR; Mach-O files get mode 755
  text:/dest=CONTENT         a small file with this content and a newline, mode 644
  rm:/dest                   unlink /dest
  mv:/src=/dest              rename within the root
  link:/dest=TARGET          make /dest a symbolic link to TARGET

The package goes to a 256 MiB raw area at the end of userdata, just below the
persistent log; the root file system does not use it. The kernel applies it at
the next boot when the command line has sweetpayload=1, logs each entry as
"SWEET PAYLOAD:", and clears the header. Writing needs the owner's confirmation:
it is a write to userdata.
"""
import hashlib, os, struct, subprocess, sys

BLOCK = 4096
LOG_BLOCKS = 1025
AREA_BLOCKS = 65536
ROOT_BYTES = 16 << 30
TGT = "/dev/block/by-name/userdata"
NW = 0x08000000 if os.name == "nt" else 0
MKDIR, FILE, LINK, RM, MOVE = 1, 2, 3, 4, 5
COUNT = [0]


def pad(data):
    return data + b"\0" * (-len(data) % BLOCK)


def entry(op, mode, size, path, target=""):
    if len(path) > 1023 or len(target) > 1023:
        sys.exit("path too long: " + path)
    COUNT[0] += 1
    return pad(struct.pack("<IIQ", op, mode, size) + path.encode().ljust(1024, b"\0") + target.encode().ljust(1024, b"\0"))


def parents(path, made, out):
    parts = path.split("/")[1:-1]
    for i in range(1, len(parts) + 1):
        d = "/" + "/".join(parts[:i])
        if d not in made and d not in ("/System", "/System/Library", "/System/Library/Extensions", "/private", "/private/var"):
            made.add(d)
            out.append(entry(MKDIR, 0o755, 0, d))


def add_file(path, mode, data, made, out):
    parents(path, made, out)
    out.append(entry(FILE, mode, len(data), path))
    out.append(pad(data))
    print(f"  {mode:o} {len(data):>10} {hashlib.sha256(data).hexdigest()[:16]} {path}")


def build(dst, items):
    made, out = set(), []
    for item in items:
        kind, _, arg = item.partition(":")
        before = len(out)
        if kind == "tree":
            local, dest = arg.rsplit("=", 1)
            for base, dirs, names in os.walk(local):
                dirs.sort()
                for name in sorted(names):
                    p = os.path.join(base, name)
                    rel = os.path.relpath(p, local).replace(os.sep, "/")
                    data = open(p, "rb").read()
                    macho = data[:4] in (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe")
                    add_file(dest + "/" + rel, 0o755 if macho else 0o644, data, made, out)
        elif kind == "text":
            dest, content = arg.split("=", 1)
            add_file(dest, 0o644, content.encode() + b"\n", made, out)
        elif kind == "link":
            dest, target = arg.split("=", 1)
            out.append(entry(LINK, 0o755, 0, dest, target))
            print("  link", dest, "->", target)
        elif kind == "mv":
            src, dest = arg.split("=", 1)
            out.append(entry(MOVE, 0, 0, src, dest))
            print("  mv", src, "->", dest)
        elif kind == "rm":
            out.append(entry(RM, 0, 0, arg))
            print("  rm", arg)
        else:
            sys.exit("unknown item: " + item)
        if len(out) == before:
            sys.exit("item produced nothing: " + item)
    blob = pad(b"SWEETPY1" + struct.pack("<II", 1, COUNT[0])) + b"".join(out)
    if len(blob) > AREA_BLOCKS * BLOCK:
        sys.exit(f"package is {len(blob)} bytes, the area holds {AREA_BLOCKS * BLOCK}")
    open(dst, "wb").write(blob)
    print(f"{COUNT[0]} entries, {len(blob)} bytes, sha256 {hashlib.sha256(blob).hexdigest()}")


def adb(a, *args, **kw):
    return subprocess.run([a, *args], capture_output=True, creationflags=NW, **kw)


def sh(a, cmd):
    return adb(a, "shell", cmd).stdout.decode().strip()


def area_start(a):
    if adb(a, "get-state").stdout.decode().strip() != "recovery":
        sys.exit("phone is not in recovery")
    real = sh(a, "readlink -f " + TGT)
    if real in sh(a, "cat /proc/mounts"):
        sys.exit("userdata is mounted")
    blocks = int(sh(a, "blockdev --getsize64 " + real)) // BLOCK
    start = blocks - LOG_BLOCKS - AREA_BLOCKS
    if start * BLOCK < ROOT_BYTES + (1 << 30):
        sys.exit("area would overlap the root image")
    return start


def write(a, path):
    data = open(path, "rb").read()
    if data[:8] != b"SWEETPY1" or len(data) % BLOCK:
        sys.exit("not a package")
    want = hashlib.sha256(data).hexdigest()
    start = area_start(a)
    count = len(data) // BLOCK
    if adb(a, "push", path, "/tmp/payload.bin").returncode:
        sys.exit("adb push failed")
    print(sh(a, f"dd if=/tmp/payload.bin of={TGT} bs={BLOCK} seek={start} conv=notrunc,fsync 2>&1; rm -f /tmp/payload.bin; sync"))
    got = sh(a, f"dd if={TGT} bs={BLOCK} skip={start} count={count} 2>/dev/null | sha256sum").split()[0]
    print("VERIFY OK" if got == want else "VERIFY FAILED", got)
    sys.exit(0 if got == want else 1)


def clear(a):
    start = area_start(a)
    print(sh(a, f"dd if=/dev/zero of={TGT} bs={BLOCK} seek={start} count=1 conv=notrunc,fsync 2>&1; sync"))


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "build":
        build(sys.argv[2], sys.argv[3:])
    elif len(sys.argv) == 4 and sys.argv[1] == "write":
        write(sys.argv[2], sys.argv[3])
    elif len(sys.argv) == 3 and sys.argv[1] == "clear":
        clear(sys.argv[2])
    else:
        sys.exit(__doc__)
