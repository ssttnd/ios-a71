#!/usr/bin/env python3
"""Add /arm-io/bluetooth to an existing ADT, with the WCN3990's Bluetooth firmware.

    tools/adt-add-bluetooth.py IN.adt OUT.adt FIRMWARE_DIR

From DC28088/ios-sweet tools/adt-add-bluetooth.py. bluetoothd and BlueTool only start when a
registry entry named "bluetooth" with local-mac-address exists; they read transport-encoding
(3 = H4BC) and transport-speed from it. bsd/dev/sweet_bt.c reads the rampatch and NVM from fw-*
properties here. It picks names by the chip's ROM version, so every crbtfw*/crnv* of the A71's
bluetooth partition goes in unless names are limited by SWEETBTFILES (space separated).
The output ADT stays local (build/, shim/out): Qualcomm firmware, never in git.
"""
import hashlib
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adt  # noqa: E402

BT_MAC = bytes.fromhex('025357424c31')     # locally administered


def cstr(s):
    return s.encode() + b'\0'


def u32(v):
    return struct.pack('<I', v)


def walk(n):
    yield n
    for c in n.children:
        yield from walk(c)


def main():
    if len(sys.argv) != 4:
        raise SystemExit(__doc__)
    src, dst, fwdir = sys.argv[1:]
    blob = open(src, 'rb').read()
    root, end = adt.parse(blob)
    assert end == len(blob), 'trailing bytes'
    armio = [c for c in root.children if c.name == 'arm-io']
    assert len(armio) == 1
    armio = armio[0]
    assert not any(c.name == 'bluetooth' for c in armio.children), 'already has bluetooth'
    top = max(struct.unpack('<I', n.prop('AAPL,phandle')[:4])[0] for n in walk(root) if n.prop('AAPL,phandle'))
    bt = adt.Node()
    bt.props = [('name', 0, cstr('bluetooth')), ('AAPL,phandle', 0, u32(top + 1)),
                ('device_type', 0, cstr('bluetooth')), ('compatible', 0, cstr('bluetooth,wcn3990')),
                ('local-mac-address', 0, BT_MAC), ('transport-encoding', 0, u32(3)),
                ('transport-speed', 0, u32(3000000)), ('vendor-id', 0, u32(0x05ac)),
                ('product-id', 0, u32(0x12a0))]
    names = os.environ.get('SWEETBTFILES', '').split() or sorted(
        f for f in os.listdir(fwdir) if f.lower().startswith(('crbtfw', 'crnv')))
    if not any(f.lower().startswith('crbtfw') for f in names) or not any(f.lower().startswith('crnv') for f in names):
        raise SystemExit('no crbtfw/crnv files in ' + fwdir)
    for f in names:
        data = open(os.path.join(fwdir, f), 'rb').read()
        # A FAT partition can give upper case names; the kernel asks for lower case ones.
        bt.props.append(('fw-' + f.lower(), 0, data))
        print('%s: %u bytes sha256 %s' % (f.lower(), len(data), hashlib.sha256(data).hexdigest()))
    armio.children.append(bt)
    out = adt.serialize(root)
    check, end2 = adt.parse(out)
    assert end2 == len(out)
    del armio.children[-1]
    assert adt.serialize(root) == blob, 'input did not round-trip'
    open(dst, 'wb').write(out)
    print('%s: %u bytes (was %u), bluetooth phandle %u' % (dst, len(out), len(blob), top + 1))


if __name__ == '__main__':
    main()
