#!/bin/bash
# Called by .github/workflows/dsc-probe.yml with the shared cache path: names for addresses from a crash line, already unslid.
c="$1"
mkdir -p probe
for a in 0x194b3858c 0x194b38924 0x1949e1884 0x1949a3114 0x1d4154ac0 0x18db98780 0x18db68ddc 0x1d4154a04 0x1d4155070 0x1d4154dd8 0x1d414ed44; do
	ipsw dyld a2s "$c" "$a" 2>&1 | tail -1 | tee -a probe/a2s.txt
done
echo done
