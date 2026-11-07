#!/usr/bin/env python3
"""Build the next package for the phone. Writes nothing to the phone.

    python tools/a71-wave3-package.py ROOT_IMAGE BLUETOOL OUT.payload

ROOT_IMAGE is a local copy of the root. launchd.plist comes from it, changed as follows:
- daemons that kept the CPU busy overnight are removed (logs/usb-wave1.txt: vmd about 50% of a core),
- the finished wallpaper rebuild job is removed,
- bluetoothd talks to the kernel's H4 control (bsd/dev/sweet_bt_hci.c, sweetbt=1).
BLUETOOL is the stub from the sweet-bt workflow; the original stays as /usr/sbin/BlueTool.orig.
"""
import copy
import importlib.util
import os
import plistlib
import shutil
import subprocess
import sys

DROP = ('com.apple.voicemail.vmd', 'com.apple.ReportCrash', 'com.apple.accessibility.axassetsd')
BT_ENV = {'BT_HCI_TRANSPORT': 'H4BC:POSIX:com.apple.uart.bluetooth,3000000'}
RERUN = '/System/Library/LaunchDaemons/org.sweet.wallpaper-rebuild.plist'
STAGE = os.path.join('build', 'wave3')


def job_key(jobs, label):
    keys = [k for k, j in jobs.items() if isinstance(j, dict) and j.get('Label') == label]
    if len(keys) != 1:
        raise SystemExit('expected one job %s, found %d' % (label, len(keys)))
    return keys[0]


def main():
    if len(sys.argv) != 4:
        raise SystemExit(__doc__)
    root, bluetool, out = sys.argv[1:]
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location('hfsread', os.path.join('tools', 'hfsread.py'))
    hfs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hfs)
    conf = plistlib.loads(hfs.HFS(root).readfile('/System/Library/xpc/launchd.plist'))
    before = copy.deepcopy(conf)
    jobs = conf['LaunchDaemons']
    for label in DROP:
        print('removed', label, jobs.pop(job_key(jobs, label)).get('ProgramArguments'))
    jobs.pop(RERUN, None)
    bt = jobs[job_key(jobs, 'com.apple.bluetoothd')]
    env = bt.setdefault('EnvironmentVariables', {})
    env.update(BT_ENV)
    env.pop('BT_DEVICE_ADDRESS', None)
    # Everything else stays as it was.
    gone = {job_key(before['LaunchDaemons'], x) for x in DROP} | {RERUN, job_key(jobs, 'com.apple.bluetoothd')}
    for key, job in before['LaunchDaemons'].items():
        if key not in gone and jobs.get(key) != job:
            raise SystemExit('unrelated job changed: ' + key)
    shutil.rmtree(STAGE, ignore_errors=True)
    os.makedirs(os.path.join(STAGE, 'System', 'Library', 'xpc'))
    with open(os.path.join(STAGE, 'System', 'Library', 'xpc', 'launchd.plist'), 'wb') as f:
        f.write(plistlib.dumps(conf, fmt=plistlib.FMT_BINARY, sort_keys=False))
    os.makedirs(os.path.join(STAGE, 'usr', 'sbin'))
    shutil.copy2(bluetool, os.path.join(STAGE, 'usr', 'sbin', 'BlueTool'))
    # The rename runs first: items apply in order.
    subprocess.run([sys.executable, '-I', '-X', 'utf8', os.path.join('tools', 'a71-payload.py'), 'build', out,
                    'mv:/usr/sbin/BlueTool=/usr/sbin/BlueTool.orig', 'tree:%s=' % STAGE], check=True,
                   creationflags=0x08000000 if os.name == 'nt' else 0)
    print(out, os.path.getsize(out), 'bytes; nothing written to the phone')


if __name__ == '__main__':
    main()
