#!/usr/bin/env python3
"""Add /arm-io/wlan to an existing ADT (sweet-mg-adt.py output) without touching anything else.

launchd starts wifid only when the device tree has a "wlan" node
(com.apple.wifid _LimitLoadToDeviceTree = wlan, marconi-wifi); the kernel's
en0 + Apple80211 (bsd/dev/flame_wlan.c) answer it. Same node as the ios-flame
mkadt.py, with a fresh unique AAPL,phandle.

    tools/adt-add-wlan.py IN.adt OUT.adt
"""
import struct, sys, hashlib
sys.path.insert(0, __import__('os').path.dirname(__file__))
import adt

src, dst = sys.argv[1], sys.argv[2]
blob = open(src, 'rb').read()
root, end = adt.parse(blob)
assert end == len(blob), 'trailing bytes'
handles = []
def walk(n):
    v = n.prop('AAPL,phandle')
    if v is not None:
        handles.append(struct.unpack('<I', v[:4])[0])
    for c in n.children:
        walk(c)
walk(root)
arm_io = [c for c in root.children if c.name == 'arm-io']
assert len(arm_io) == 1
arm_io = arm_io[0]
assert not any(c.name == 'wlan' for c in arm_io.children), 'already has wlan'
wlan = adt.Node()
wlan.props = [('name', 0, b'wlan\0'), ('AAPL,phandle', 0, struct.pack('<I', max(handles) + 1)),
              ('device_type', 0, b'wlan\0'), ('compatible', 0, b'flame,wcn3990\0')]
arm_io.children.append(wlan)
out = adt.serialize(root)
check, end2 = adt.parse(out)
assert end2 == len(out)
# Everything else byte-identical: the old tree re-serializes to the input.
del arm_io.children[-1]
assert adt.serialize(root) == blob, 'input did not round-trip'
open(dst, 'wb').write(out)
print(f'{dst}: {len(out)} bytes (was {len(blob)}), wlan phandle {max(handles) + 1}, sha256 {hashlib.sha256(out).hexdigest()}')
