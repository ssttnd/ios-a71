#!/bin/bash
# Called by .github/workflows/dsc-probe.yml with the shared cache path. What accountsd does for tetheredSyncSourceTypeForDataclass.
c="$1"
mkdir -p probe
ipsw dyld macho "$c" Accounts --objc 2>&1 | grep -i -E 'tethered' | head -20 | tee probe/objc.txt
for s in "-[ACAccountStore tetheredSyncSourceTypeForDataclass:]" "-[ACDAccountStore tetheredSyncSourceTypeForDataclass:completion:]" "-[ACRemoteAccountStoreSession tetheredSyncSourceTypeForDataclass:completion:]"; do
	echo "== $s"
	ipsw dyld disass "$c" --symbol "$s" 2>&1 | grep -E 'bl|Error|error' | head -40 | cut -c1-120 | tee -a probe/disass.txt
done
