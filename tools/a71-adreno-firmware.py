#!/usr/bin/env python3
"""Add the A71's own GPU firmware to a boot ADT (from DC28088/ios-sweet).

Firmware stays in the local boot image; this tool does not download it.
"""
import argparse
import hashlib
from pathlib import Path
import struct
import zlib

import adt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('base_adt', type=Path)
    parser.add_argument('gmu_firmware', type=Path)
    parser.add_argument('output_adt', type=Path)
    parser.add_argument('--sha256', required=True,
                        help='Expected SHA-256 of the locally obtained firmware')
    parser.add_argument('--sqe-firmware', type=Path)
    parser.add_argument('--sqe-sha256')
    parser.add_argument('--zap-metadata', type=Path)
    parser.add_argument('--zap-metadata-sha256')
    parser.add_argument('--zap-code', type=Path)
    parser.add_argument('--zap-code-sha256')
    args = parser.parse_args()
    firmware = args.gmu_firmware.read_bytes()
    digest = hashlib.sha256(firmware).hexdigest()
    if digest != args.sha256.lower():
        parser.error(f'GMU SHA-256 mismatch: got {digest}')
    if len(firmware) != 0x8000:
        parser.error('A618 legacy GMU image must be exactly 32768 bytes')
    sp, pc = struct.unpack_from('<II', firmware)
    if sp % 8 or not 0x40000 <= sp <= 0x44000 or not pc & 1 or pc & ~1 >= len(firmware):
        parser.error('GMU image has an invalid Cortex-M vector table')
    source = args.base_adt.read_bytes()
    root, end = adt.parse(source)
    if end != len(source):
        parser.error('Base ADT has trailing data')
    if any(child.name == 'sweet-adreno' for child in root.children):
        parser.error('Base ADT already contains sweet-adreno; use the original tree')
    node = adt.Node()
    node.props = [('name', 0, b'sweet-adreno\0'),
                  ('compatible', 0, b'sweet,adreno618\0'),
                  ('gmu-firmware', 0, firmware),
                  ('gmu-crc32', 0, struct.pack('<I', zlib.crc32(firmware))),
                  ('gmu-sha256', 0, bytes.fromhex(digest))]
    optional = [('sqe-firmware', args.sqe_firmware, args.sqe_sha256, 32304),
                ('zap-metadata', args.zap_metadata, args.zap_metadata_sha256, 0x1acc),
                ('zap-code', args.zap_code, args.zap_code_sha256, 0x7b0)]
    if bool(args.zap_metadata) != bool(args.zap_code):
        parser.error('Both signed ZAP metadata and code are required')
    for name, path, expected, size in optional:
        if path is None:
            if expected: parser.error(f'{name}: checksum supplied without file')
            continue
        blob = path.read_bytes()
        if len(blob) != size or not expected or hashlib.sha256(blob).hexdigest() != expected.lower():
            parser.error(f'{name}: expected size and SHA-256 are required and must match')
        node.props.extend([(name, 0, blob),
                           (name + '-crc32', 0, struct.pack('<I', zlib.crc32(blob)))])
    if args.zap_metadata:
        # A71 live DT gpu_region.
        node.props.extend([('zap-reserved-base', 0, struct.pack('<Q', 0x9a915000)),
                           ('zap-reserved-size', 0, struct.pack('<I', 0x2000))])
    root.children.append(node)
    output = adt.serialize(root)
    # Exclusive create preserves the chosen baseline and previous images.
    with args.output_adt.open('xb') as stream:
        stream.write(output)
    print(f'Wrote {args.output_adt}: GMU {len(firmware)} bytes, SHA-256 {digest}')


if __name__ == '__main__':
    main()
