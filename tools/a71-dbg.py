#!/usr/bin/env python3
"""Client for sweetdbg (userspace/debug/sweetdbg.c) on the A71 over Wi-Fi, TCP 7777.

The key is in build/sweetdbg.key (made by `a71-dbg.py newkey`, packaged to /var/local/sweetdbg.key; not in git).
The phone's address: --host, or A71_HOST, or build/sweetdbg.host, or a scan of the PC's /24 for port 7777.

  a71-dbg.py run /var/local/bin/tasprobe            run a program, print its output and exit status
  a71-dbg.py get /var/mobile/x.plist out.plist      fetch a file
  a71-dbg.py put local.bin /var/local/bin/x [755]   send a file (atomic rename on the phone)
  a71-dbg.py ls /var/local | rm PATH | mkdir PATH | ps | kill PID [SIG]
  a71-dbg.py shot [out.png]                         screenshot of the frame being shown (default build/shot.png)
  a71-dbg.py tap X Y | swipe X1 Y1 X2 Y2 [MS] | key power|volup|voldown|home [MS]   (panel pixels 1080x2400)
  a71-dbg.py rd PA [COUNT] | wr PA VALUE | sysctl NAME
  a71-dbg.py restart                                re-exec sweetdbg (after a put of a new one)
  a71-dbg.py newkey                                 write a new random key to build/sweetdbg.key
"""
import ipaddress, os, secrets, socket, struct, sys, zlib, concurrent.futures

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEYF = os.path.join(ROOT, "build", "sweetdbg.key")
HOSTF = os.path.join(ROOT, "build", "sweetdbg.host")
PORT = 7777
KEYS = {"power": 0, "volup": 1, "voldown": 2, "home": 3}


def key():
    try:
        return open(KEYF).read().strip()
    except OSError:
        sys.exit("no build/sweetdbg.key: run `a71-dbg.py newkey` and package it")


def local_nets():
    nets = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.168.0.1", 9))
        nets.add(ipaddress.ip_network(s.getsockname()[0] + "/24", strict=False))
        s.close()
    except OSError:
        pass
    return nets


def probe(ip):
    try:
        with socket.create_connection((str(ip), PORT), timeout=0.6):
            return str(ip)
    except OSError:
        return None


def host(args):
    if "--host" in args:
        i = args.index("--host")
        h = args[i + 1]
        del args[i:i + 2]
        return h
    if os.environ.get("A71_HOST"):
        return os.environ["A71_HOST"]
    if os.path.exists(HOSTF):
        h = open(HOSTF).read().strip()
        if probe(h):
            return h
    for net in local_nets():
        with concurrent.futures.ThreadPoolExecutor(64) as ex:
            for r in ex.map(probe, net.hosts()):
                if r:
                    open(HOSTF, "w").write(r)
                    return r
    sys.exit("phone not found on port 7777 (is it on Wi-Fi and is sweetdbg running?)")


def request(h, fields, payload=b"", timeout=180):
    s = socket.create_connection((h, PORT), timeout=timeout)
    s.sendall(("\t".join([key()] + [str(f) for f in fields]) + "\n").encode())
    if payload:
        s.sendall(payload)
    return s


def read_line(s):
    b = b""
    while not b.endswith(b"\n"):
        c = s.recv(1)
        if not c:
            break
        b += c
    return b.decode(errors="replace").strip()


def read_n(s, n):
    out = bytearray()
    while len(out) < n:
        c = s.recv(min(1 << 20, n - len(out)))
        if not c:
            break
        out += c
    return bytes(out)


def stream(s):
    while True:
        c = s.recv(65536)
        if not c:
            break
        sys.stdout.buffer.write(c)
        sys.stdout.flush()


def png(path, w, h, stride, data):
    rows = bytearray()
    for y in range(h):
        r = data[y * stride:y * stride + w * 4]
        rgb = bytearray(w * 3)
        rgb[0::3] = r[2::4]
        rgb[1::3] = r[1::4]
        rgb[2::3] = r[0::4]
        rows += b"\0" + rgb

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) +
                chunk(b"IDAT", zlib.compress(bytes(rows), 6)) + chunk(b"IEND", b""))


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return
    if args[0] == "newkey":
        os.makedirs(os.path.dirname(KEYF), exist_ok=True)
        open(KEYF, "w").write(secrets.token_hex(24) + "\n")
        print("new key in", KEYF)
        return
    h = host(args)
    cmd, rest = args[0], args[1:]
    if cmd == "get":
        s = request(h, ["get", rest[0]])
        head = read_line(s)
        if not head.startswith("OK "):
            sys.exit(head)
        data = read_n(s, int(head.split()[1]))
        out = rest[1] if len(rest) > 1 else os.path.basename(rest[0])
        open(out, "wb").write(data)
        print(f"{len(data)} bytes -> {out}")
    elif cmd == "put":
        data = open(rest[0], "rb").read()
        mode = rest[2] if len(rest) > 2 else "755"
        s = request(h, ["put", rest[1], mode, len(data)], data)
        print(read_line(s))
    elif cmd == "shot":
        out = rest[0] if rest else os.path.join(ROOT, "build", "shot.png")
        s = request(h, ["shot"])
        head = read_line(s)
        if not head.startswith("OK "):
            sys.exit(head)
        n, w, hh, stride = (int(x) for x in head.split()[1:5])
        data = read_n(s, n)
        png(out, w, hh, stride, data)
        print(f"{w}x{hh} -> {out}")
    elif cmd == "key":
        k = str(KEYS.get(rest[0], rest[0]))
        s = request(h, ["key", k] + rest[1:])
        print(read_line(s))
    elif cmd == "threads" and len(rest) == 1:
        s = request(h, ["threads", rest[0]])
        while True:
            line = read_line(s)
            print(line)
            if line.startswith(("OK", "ERR")):
                break
    elif cmd == "kill" and len(rest) == 1:
        s = request(h, ["kill", rest[0], 15])
        print(read_line(s))
    elif cmd == "swipe" and len(rest) == 4:
        s = request(h, ["swipe"] + rest + [300])
        print(read_line(s))
    else:
        s = request(h, [cmd] + rest)
        stream(s)


if __name__ == "__main__":
    main()
