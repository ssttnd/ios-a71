#!/usr/bin/env python3
"""Write a boot image to the A71 from TWRP, verify it by readback, reboot into iOS.

  a71-flash-boot.py ADB_EXE IMAGE EXPECTED_SHA256

Destructive: overwrites the start of the boot partition. Confirm with the owner
first. Uses a plain system reboot: `adb reboot` from TWRP returns to recovery.
"""
import hashlib, subprocess, sys

ADB, IMG, EXPECT = sys.argv[1:4]
TGT = "/dev/block/by-name/boot"


def adb(*a):
    return subprocess.check_output([ADB, *a]).decode(errors="replace").strip()


def sh(c):
    return adb("shell", c)


img = open(IMG, "rb").read()
h = hashlib.sha256(img).hexdigest()
if h != EXPECT:
    sys.exit(f"image hash {h} is not the expected one")
if img[:8] != b"ANDROID!" or len(img) % 4096 or len(img) >= 64 << 20:
    sys.exit("not a boot image of the expected form")
if adb("get-state") != "recovery":
    sys.exit("phone is not in recovery")
print("current boot:", sh(f"dd if={TGT} bs=1048576 count=64 2>/dev/null | sha256sum").split()[0][:16])
adb("push", IMG, "/tmp/new-boot.img")
if sh("sha256sum /tmp/new-boot.img").split()[0] != h:
    sys.exit("push corrupted the image")
print(sh(f"dd if=/tmp/new-boot.img of={TGT} bs=4096 conv=fsync 2>&1; sync"))
got = sh(f"dd if={TGT} bs=4096 count={len(img) // 4096} 2>/dev/null | sha256sum").split()[0]
print("readback", got)
if got != h:
    sys.exit("VERIFY FAILED: not rebooting; restore boot from the backup")
sh("rm -f /tmp/new-boot.img")
print("VERIFY OK")
subprocess.Popen([ADB, "shell", "setprop sys.powerctl reboot"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
print("reboot requested")
