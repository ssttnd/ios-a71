#!/usr/bin/env python3
"""Give lockdownd's four FairPlay MIG calls a timeout (iOS 16.7, 20H392 lockdownd).

fairplayd.H2 never stays up on the A71, so without this lockdownd blocks in main() on its first
FairPlay mach_msg and never accepts: every lockdown_copy_value caller hangs (Notes via accountsd).

  a71-patch-lockdownd.py IN OUT     then: a71-dbg.py put OUT /usr/libexec/lockdownd 755

The original stays on the phone in /var/local/apple-orig/lockdownd. Neither file goes in git.
"""
import struct
import sys

SITES = [(0x2b13c, 0x2b148), (0x2b398, 0x2b3a4), (0x2b51c, 0x2b528), (0x2b6d0, 0x2b6dc)]
MOV_W1_3, MOV_W1_TIMEOUTS = 0x52800061, 0x52802261  # SEND|RCV -> SEND|RCV|SEND_TIMEOUT|RCV_TIMEOUT
MOV_W5_0, MOV_W5_500 = 0x52800005, 0x52803E85  # timeout 500 ms


def main():
    data = bytearray(open(sys.argv[1], "rb").read())
    for w1, w5 in SITES:
        if struct.unpack_from("<I", data, w1)[0] != MOV_W1_3 or struct.unpack_from("<I", data, w5)[0] != MOV_W5_0:
            sys.exit(f"unexpected code at {w1:#x}: wrong lockdownd build or already patched")
        struct.pack_into("<I", data, w1, MOV_W1_TIMEOUTS)
        struct.pack_into("<I", data, w5, MOV_W5_500)
    open(sys.argv[2], "wb").write(data)


if __name__ == "__main__":
    main()
