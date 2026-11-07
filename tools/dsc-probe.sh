#!/bin/bash
# Called by .github/workflows/dsc-probe.yml with the shared cache path. Every step may fail on its own.
# ipsw 3.1: `dyld str` has no --image; `dyld disass` takes --symbol OR --image, not both.
c="$1"
mkdir -p probe
step() { echo; echo "===== $*"; }

step "ipsw version"; ipsw version
step "help: xref"; ipsw dyld xref --help 2>&1 | head -40
step "help: disass"; ipsw dyld disass --help 2>&1 | head -50

for s in "unsupported extended range format" "CAMetalLayerInvalid" "extended range"; do
	step "strings: $s"
	ipsw dyld str "$c" "$s" 2>&1 | tee -a probe/str.txt
done

addrs=$(grep -E 'QuartzCore' probe/str.txt | grep -oE '0x[0-9a-fA-F]{9,}' | sort -u | head -6)
for a in $addrs; do
	step "xref $a"
	ipsw dyld xref "$c" "$a" 2>&1 | head -60 | tee -a probe/xref1.txt
	ipsw dyld xref "$c" "$a" --all 2>&1 | head -60 | tee -a probe/xref1.txt
done
refs=$(grep -oE '0x[0-9a-fA-F]{9,}' probe/xref1.txt 2>/dev/null | sort -u | head -16)
for r in $refs; do
	step "xref2 $r"
	ipsw dyld xref "$c" "$r" 2>&1 | head -40 | tee -a probe/xref2.txt
	step "a2s $r"
	ipsw dyld a2s "$c" "$r" 2>&1 | tee -a probe/a2s.txt
done
for r in $(grep -oE '0x[0-9a-fA-F]{9,}' probe/xref2.txt 2>/dev/null | sort -u | head -16); do
	step "a2s $r"
	ipsw dyld a2s "$c" "$r" 2>&1 | tee -a probe/a2s.txt
done

for sym in "__ZL32layer_private_check_pixel_formatP20_CAMetalLayerPrivatej" \
	   "__ZL31layer_private_set_device_formatP20_CAMetalLayerPrivateP11objc_object" \
	   "-[CAMetalLayer setDevice:]" "_MTLCreateSystemDefaultDevice" "_MTLCopyAllDevices" \
	   "-[CAMetalLayer setColorspace:nonDefault:]"; do
	step "disass $sym"
	ipsw dyld disass "$c" --symbol "$sym" 2>&1 | head -600 | tee -a probe/disass.txt
done
# every function the string's references name
grep -oE '[-+]\[[A-Za-z0-9_]+ [A-Za-z0-9_:]+\]' probe/a2s.txt 2>/dev/null | sort -u | head -10 > probe/funcs.txt
grep -oE '__Z[A-Za-z0-9_]+|CA::[A-Za-z0-9_:]+' probe/a2s.txt 2>/dev/null | sort -u | head -10 >> probe/funcs.txt
while read -r f; do
	step "disass $f"
	ipsw dyld disass "$c" --symbol "$f" 2>&1 | head -900 | tee -a probe/disass.txt
done < probe/funcs.txt
for r in $(grep -oE '0x[0-9a-fA-F]{9,}' probe/xref2.txt probe/xref1.txt 2>/dev/null | sort -u | head -8); do
	step "disass around $r"
	start=$(printf '0x%x' $(( r - 0x200 )))
	ipsw dyld disass "$c" --vaddr "$start" --count 300 2>&1 | tee -a probe/disass-around.txt
done
step "done"
