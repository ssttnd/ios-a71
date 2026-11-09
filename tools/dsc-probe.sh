#!/bin/bash
# Called by .github/workflows/dsc-probe.yml with the shared cache path: names for addresses from a crash line, already unslid.
c="$1"
mkdir -p probe
for a in 0x1a466086c 0x1a4616e8c 0x1a4616e8c 0x1a46189e8 0x1a4613ad4 0x1a461649c 0xcbe9013c 0x1949a3114 0x1949a2f10 0x1949a3114 0x1949a2f10 0x1949a3114; do
	ipsw dyld a2s "$c" "$a" 2>&1 | tail -1 | tee -a probe/a2s.txt
done
echo done
