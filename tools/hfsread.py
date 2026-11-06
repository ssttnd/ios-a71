#!/usr/bin/env python3
"""Minimal read-only HFS+/HFSX reader: stat and read files from an image file.
usage: hfsread.py IMAGE PATH [PATH...]   prints size and, for plists, the content.
       hfsread.py IMAGE --dump PATH OUT
Never writes to IMAGE. Ignores the journal (fine for a cleanly built image)."""
import struct, sys, plistlib


class HFS:
    def __init__(self, path):
        self.f = open(path, 'rb')
        h = self.read(1024, 512)
        assert h[:2] in (b'H+', b'HX'), h[:2]
        self.sig = h[:2]
        self.attrs = struct.unpack('>I', h[4:8])[0]
        self.lastmount = h[8:12]
        self.files, self.folders, self.bs, self.total = struct.unpack('>IIII', h[32:48])
        self.catfork = h[272:352]
        self.extfork = h[192:272]
        self.cat_ext = self.fork_extents(self.catfork)
        hdr = self.fork_read(self.cat_ext, 0, 512)
        self.root_node = struct.unpack('>I', hdr[16:20])[0]
        self.node_size = struct.unpack('>H', hdr[14 + 18:14 + 20])[0]
        self.keycmp = hdr[14 + 37]  # 0xBC binary compare (HFSX), 0xCF case folding

    def read(self, off, n):
        self.f.seek(off)
        return self.f.read(n)

    @staticmethod
    def fork_extents(fork):
        return [struct.unpack('>II', fork[16 + i * 8:24 + i * 8]) for i in range(8)]

    def fork_read(self, exts, off, n):
        out = b''
        pos = 0
        for start, count in exts:
            ln = count * self.bs
            if count and off < pos + ln and n > 0:
                s = off - pos
                t = min(n, ln - s)
                out += self.read(start * self.bs + s, t)
                off += t
                n -= t
            pos += ln
        assert n == 0, 'fork needs overflow extents (not implemented)'
        return out

    def node(self, i):
        d = self.fork_read(self.cat_ext, i * self.node_size, self.node_size)
        kind, height = d[8], d[9]
        cnt = struct.unpack('>H', d[10:12])[0]
        offs = [struct.unpack('>H', d[self.node_size - 2 * (k + 1):self.node_size - 2 * k])[0] for k in range(cnt)]
        return kind, cnt, offs, d

    @staticmethod
    def key(d, o):
        kl = struct.unpack('>H', d[o:o + 2])[0]
        parent = struct.unpack('>I', d[o + 2:o + 6])[0]
        nl = struct.unpack('>H', d[o + 6:o + 8])[0]
        name = d[o + 8:o + 8 + nl * 2].decode('utf-16-be')
        return kl, parent, name

    def cmp(self, a, b):
        if self.sig == b'HX':
            return (a > b) - (a < b)
        a, b = a.lower(), b.lower()
        return (a > b) - (a < b)

    def lookup(self, parent, name):
        i = self.root_node
        tgt = name.encode('utf-16-be')
        while True:
            kind, cnt, offs, d = self.node(i)
            best = None
            for k in range(cnt):
                kl, p, n = self.key(d, offs[k])
                c = (p > parent) - (p < parent) or self.cmp(n.encode('utf-16-be'), tgt)
                if kind == 0xff:  # leaf
                    if c == 0:
                        rec = offs[k] + 2 + kl
                        rec += rec & 1 and 0
                        return d[rec:rec + 248]
                else:
                    if c <= 0:
                        best = struct.unpack('>I', d[offs[k] + 2 + kl:offs[k] + 6 + kl])[0]
            if kind == 0xff or best is None:
                return None
            i = best

    def resolve(self, path):
        cnid = 2
        rec = None
        for part in [p for p in path.split('/') if p]:
            rec = self.lookup(cnid, part)
            if rec is None:
                return None
            rt = struct.unpack('>h', rec[:2])[0]
            cnid = struct.unpack('>I', rec[8:12])[0]
        return rec

    def info(self, path):
        rec = self.resolve(path)
        if rec is None:
            return None
        rt = struct.unpack('>h', rec[:2])[0]
        if rt == 1:
            return {'type': 'dir', 'cnid': struct.unpack('>I', rec[8:12])[0]}
        fork = rec[88:168]
        size = struct.unpack('>Q', fork[:8])[0]
        return {'type': 'file', 'cnid': struct.unpack('>I', rec[8:12])[0], 'size': size,
                'ext': self.fork_extents(fork), 'flags': struct.unpack('>H', rec[2:4])[0]}

    def readfile(self, path):
        i = self.info(path)
        return self.fork_read(i['ext'], 0, i['size'])


if __name__ == '__main__':
    h = HFS(sys.argv[1])
    print(f"sig={h.sig} attrs={h.attrs:#x} lastmount={h.lastmount} files={h.files} folders={h.folders} bs={h.bs} total={h.total} keycmp={h.keycmp:#x}")
    if sys.argv[2] == '--dump':
        open(sys.argv[4], 'wb').write(h.readfile(sys.argv[3]))
        sys.exit()
    for p in sys.argv[2:]:
        i = h.info(p)
        print('==', p, '->', {k: v for k, v in (i or {}).items() if k != 'ext'} if i else 'MISSING')
        if i and i['type'] == 'file' and p.endswith('.plist') and i['size'] < 200000:
            try:
                v = plistlib.loads(h.readfile(p))
                s = repr(v)
                print('   ', s[:900])
            except Exception as e:
                print('    (plist err)', e)
