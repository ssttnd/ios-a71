#!/bin/bash
# Called by .github/workflows/dsc-probe.yml with the shared cache path: names for addresses from a crash line, already unslid.
c="$1"
mkdir -p probe
for a in 0x1c2611030 0x1c2622b18 0x1c2622db8 0x1c2611524 0x18db6a308 0x18db6a694 0x1d1bee0ec 0x18120c224 0x1811a5cb0 0x1811a548c 0x1811a4ad4 0x1a1e5c5a0; do
	ipsw dyld a2s "$c" "$a" 2>&1 | tail -1 | tee -a probe/a2s.txt
done
echo done
