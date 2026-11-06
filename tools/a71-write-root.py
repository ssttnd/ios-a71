#!/usr/bin/env python3
"""Write an iOS root image to the A71's userdata through TWRP, resumably.

  a71-write-root.py ADB_EXE IMAGE EXPECTED_SHA256

Destructive: overwrites the start of userdata. Confirm with the owner first.
Compares each 1 GiB chunk by SHA-256, rewrites only those that differ, then
verifies the whole image by readback. A single long stream is not used because
TWRP's dd failed on one at about 15 GiB (read error: Bad address).
Run it from a scratch directory: it writes a small rx.sh to the current directory.
"""
import hashlib, os, socket, subprocess, sys, time

ADB, IMG, EXPECT = sys.argv[1:4]
PORT = 15555
TGT = "/dev/block/by-name/userdata"
CH_MIB = 1024


def sh(cmd):
    return subprocess.check_output([ADB, "shell", cmd]).decode().strip()


def phone_hash(i):
    return sh(f"dd if={TGT} bs=1048576 skip={i * CH_MIB} count={CH_MIB} 2>/dev/null | sha256sum").split()[0]


def send(i, data):
    rx = f"#!/system/bin/sh\nexec dd of={TGT} bs=1048576 seek={i * CH_MIB} conv=notrunc,fsync 2>/tmp/dd.log\n"
    with open("rx.sh", "w", newline="\n") as f:
        f.write(rx)
    subprocess.check_call([ADB, "push", "rx.sh", "/tmp/rx.sh"], stdout=subprocess.DEVNULL)
    subprocess.check_call([ADB, "forward", f"tcp:{PORT}", f"tcp:{PORT}"], stdout=subprocess.DEVNULL)
    srv = subprocess.Popen([ADB, "shell", f"nc -l -p {PORT} sh /tmp/rx.sh; echo NC_EXIT $?"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    time.sleep(2)
    try:
        s = socket.create_connection(("127.0.0.1", PORT))
        s.sendall(data)
        s.shutdown(socket.SHUT_WR)
        srv.communicate(timeout=300)
        s.close()
    except OSError as e:
        print(f"  chunk {i}: {e}; dd: {sh('cat /tmp/dd.log')}", flush=True)
        srv.kill()
    subprocess.call([ADB, "forward", "--remove", f"tcp:{PORT}"])


size = os.path.getsize(IMG)
if size % (CH_MIB << 20):
    sys.exit("image size is not a whole number of chunks")
nch = size // (CH_MIB << 20)
if subprocess.check_output([ADB, "get-state"]).decode().strip() != "recovery":
    sys.exit("phone is not in recovery")
real = sh("readlink -f " + TGT)
if size > int(sh("blockdev --getsize64 " + real)) - (1025 * 4096):
    sys.exit("image would overlap the persistent log")
if real in sh("cat /proc/mounts"):
    sys.exit("userdata is mounted")

full = hashlib.sha256()
with open(IMG, "rb") as f:
    for i in range(nch):
        data = f.read(CH_MIB << 20)
        full.update(data)
        want = hashlib.sha256(data).hexdigest()
        have = phone_hash(i)
        tries = 0
        wrote = have != want
        while have != want:
            tries += 1
            if tries > 5:
                sys.exit(f"chunk {i}: still differs after 5 tries")
            send(i, data)
            sh("sync")
            have = phone_hash(i)
        print(f"chunk {i:2d}: {'written x' + str(tries) if wrote else 'already correct'}", flush=True)
if full.hexdigest() != EXPECT:
    sys.exit("local image does not match EXPECTED_SHA256")
got = sh(f"dd if={TGT} bs=1048576 count={nch * CH_MIB} 2>/dev/null | sha256sum").split()[0]
print("VERIFY OK" if got == EXPECT else "VERIFY FAILED", got, flush=True)
sys.exit(0 if got == EXPECT else 1)
