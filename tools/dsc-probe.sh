#!/bin/bash
# Called by .github/workflows/dsc-probe.yml with the shared cache path. Every step may fail on its own.
c="$1"
mkdir -p probe
step() { echo; echo "===== $*"; }

step "ipsw version"; ipsw version

for s in "unsupported extended range format" "CAMetalLayerInvalid"; do
	step "strings: $s"
	ipsw dyld str "$c" "$s" --image QuartzCore 2>&1 | tee -a probe/str.txt || ipsw dyld str "$c" "$s" 2>&1 | tee -a probe/str.txt
done

# every address the string search printed, then who refers to it, twice (cstring -> CFString -> code)
addrs=$(grep -oE '0x[0-9a-fA-F]{9,}' probe/str.txt | sort -u | head -6)
for a in $addrs; do
	step "xref $a"
	ipsw dyld xref "$c" "$a" --image QuartzCore 2>&1 | tee -a probe/xref1.txt
	ipsw dyld xref "$c" "$a" --all 2>&1 | head -40 | tee -a probe/xref1.txt
done
refs=$(grep -oE '0x[0-9a-fA-F]{9,}' probe/xref1.txt | sort -u | grep -v -F "$(echo "$addrs")" | head -12)
for r in $refs; do
	step "xref2 $r"
	ipsw dyld xref "$c" "$r" --image QuartzCore 2>&1 | tee -a probe/xref2.txt
done
code=$(cat probe/xref1.txt probe/xref2.txt 2>/dev/null | grep -oE '0x[0-9a-fA-F]{9,}' | sort -u | head -24)
for r in $code; do
	step "a2s $r"
	ipsw dyld a2s "$c" "$r" 2>&1 | tee -a probe/a2s.txt
done
# disassemble each function that names the string
grep -oE '(\-|\+)\[[^]]+\]|_?[A-Za-z_][A-Za-z0-9_:]*CA[A-Za-z0-9_:]*' probe/a2s.txt | sort -u | head -12 > probe/funcs.txt
while read -r f; do
	step "disass $f"
	ipsw dyld disass "$c" --image QuartzCore --symbol "$f" 2>&1 | head -900 | tee -a probe/disass-quartzcore.txt
done < probe/funcs.txt
# and the raw neighbourhood of every code reference
for r in $code; do
	step "disass around $r"
	start=$(printf '0x%x' $(( r - 0x300 )))
	ipsw dyld disass "$c" --vaddr "$start" --count 400 2>&1 | tee -a probe/disass-around.txt
done

for sym in "-[NUDevice_iOS initWithName:]" "-[NUDevice_iOS init]" "+[NUPlatform_iOS sharedPlatform]" "-[NUPlatform_iOS init]"; do
	step "disass NeutrinoCore $sym"
	ipsw dyld disass "$c" --image NeutrinoCore --symbol "$sym" 2>&1 | head -700 | tee -a probe/disass-neutrino.txt
done
step "NeutrinoCore strings near NUDevice"
ipsw dyld str "$c" "NUDevice" --image NeutrinoCore 2>&1 | head -60
ipsw dyld str "$c" "supportsFamily" 2>&1 | head -20
step "done"
