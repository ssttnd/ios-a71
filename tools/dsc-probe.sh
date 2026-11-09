#!/bin/bash
# Called by .github/workflows/dsc-probe.yml with the shared cache path: names for addresses from a crash line, already unslid.
c="$1"
mkdir -p probe
for a in 0x1c2611030 0x1c2622b18 0x1c2622db8 0x1c2611524 0x186e5c148 0x186e5d2e0 0x186e61d20 0x1811b0ef8 0x1811b0da4 0xb81ab0d8 0x1a4624344; do
	ipsw dyld a2s "$c" "$a" 2>&1 | tail -1 | tee -a probe/a2s.txt
done
echo done
