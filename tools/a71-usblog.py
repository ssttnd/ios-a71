#!/usr/bin/env python3
"""Live iOS kernel log from the A71 over USB, on Windows, with no extra packages.

  a71-usblog.py [OUTPUT.txt]

Needs a boot image with sweetusb=3. Waits for the phone's serial port
(VID 1209 PID 0002, CDC-ACM, driven by Windows' own usbser), prints the console
and appends it to OUTPUT (default logs/usb-TIMESTAMP.txt). It reconnects when the
phone reboots. Stop with Ctrl+C. The port is found by USB ID, not by COM number.
"""
import ctypes, ctypes.wintypes as wt, datetime, os, re, subprocess, sys, time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.CreateFileW.restype = wt.HANDLE
k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p, wt.DWORD, wt.DWORD, wt.HANDLE]
k32.ReadFile.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
k32.CloseHandle.argtypes = [wt.HANDLE]
INVALID = wt.HANDLE(-1).value


class Timeouts(ctypes.Structure):
    _fields_ = [(n, wt.DWORD) for n in ("ri", "rm", "rc", "wm", "wc")]


def find_port():
    ps = ("Get-CimInstance Win32_PnPEntity -Filter \"PNPDeviceID LIKE 'USB\\\\VID_1209&PID_0002%'\" "
          "| Select-Object -ExpandProperty Name")
    # CREATE_NO_WINDOW: without it a detached run opens a console window on every poll.
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, creationflags=0x08000000).stdout
    m = re.search(r"\((COM\d+)\)", out)
    return m.group(1) if m else None


def read_port(port, log):
    h = k32.CreateFileW("\\\\.\\" + port, 0x80000000, 0, None, 3, 0, None)
    if h == INVALID or h is None:
        print(f"[cannot open {port}: error {ctypes.get_last_error()}]", flush=True)
        return
    try:
        # Return as soon as anything arrived, or after 200 ms of nothing.
        k32.SetCommTimeouts(h, ctypes.byref(Timeouts(0xffffffff, 0xffffffff, 200, 0, 0)))
        k32.EscapeCommFunction(h, 5)        # SETDTR
        print(f"[reading {port}]", flush=True)
        buf = ctypes.create_string_buffer(65536)
        got = wt.DWORD()
        while k32.ReadFile(h, buf, len(buf), ctypes.byref(got), None):
            if got.value:
                data = buf.raw[:got.value]
                log.write(data)
                log.flush()
                sys.stdout.write(data.decode("utf-8", "replace").replace("\r", ""))
                sys.stdout.flush()
    finally:
        k32.CloseHandle(h)
    print("\n[port closed]", flush=True)


def main():
    if len(sys.argv) > 1:
        path = sys.argv[1]
    else:
        path = os.path.join("logs", datetime.datetime.now().strftime("usb-%Y%m%d-%H%M%S.txt"))
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    print(f"[log: {os.path.abspath(path)}; waiting for USB VID 1209 PID 0002]", flush=True)
    with open(path, "ab") as log:
        while True:
            port = find_port()
            if port:
                read_port(port, log)
            time.sleep(2)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
