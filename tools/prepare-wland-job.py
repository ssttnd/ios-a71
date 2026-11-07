#!/usr/bin/env python3
"""Prepare a separate merged launchd plist that adds the wland boot job.

wland (userspace/wifi, ios-flame sunfish phone/wifi) brings the WCN3990 up once per
boot (`wland auto`, log /var/local/wland.log). It does nothing when /var/local/wland.off
exists or the kernel was booted with sweetwifi=0, and needs /dev/pmem (sweetpmem=1).
Every other job must stay byte-for-byte as it was.
"""
import argparse
import copy
import hashlib
import json
import plistlib
from pathlib import Path

KEY = '/System/Library/LaunchDaemons/org.ios-sweet.wland.plist'
JOB = {
    'Label': 'org.ios-sweet.wland',
    'ProgramArguments': ['/var/local/bin/wland', 'auto'],
    'RunAtLoad': True,
    'UserName': 'root',
    'EnvironmentVariables': {'PATH': '/var/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'},
}

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('input', type=Path)
p.add_argument('output', type=Path)
a = p.parse_args()
if a.output.exists() or a.input.resolve() == a.output.resolve():
    raise SystemExit('Output must be a new file; input remains unchanged')
original = a.input.read_bytes()
config = plistlib.loads(original)
before = copy.deepcopy(config)
jobs = config.get('LaunchDaemons')
if not isinstance(jobs, dict):
    raise SystemExit('Missing merged LaunchDaemons dictionary')
if KEY in jobs or any(j.get('Label') == JOB['Label'] for j in jobs.values()):
    raise SystemExit('wland job already present')
jobs[KEY] = JOB
for key, old_job in before['LaunchDaemons'].items():
    if config['LaunchDaemons'][key] != old_job:
        raise SystemExit('Unrelated launch job changed')
if {k: v for k, v in config.items() if k != 'LaunchDaemons'} != {k: v for k, v in before.items() if k != 'LaunchDaemons'}:
    raise SystemExit('Non-daemon section changed')
output = plistlib.dumps(config, fmt=plistlib.FMT_BINARY)
a.output.parent.mkdir(parents=True, exist_ok=True)
a.output.write_bytes(output)
print(json.dumps({'input_sha256': hashlib.sha256(original).hexdigest(),
                  'output_sha256': hashlib.sha256(output).hexdigest(),
                  'jobs_before': len(before['LaunchDaemons']), 'jobs_after': len(jobs),
                  'added': KEY, 'phone_written': False}))
