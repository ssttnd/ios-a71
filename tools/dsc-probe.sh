#!/bin/bash
# Called by .github/workflows/dsc-probe.yml with the shared cache path. Every step may fail on its own.
# ipsw 3.1: `dyld disass` takes --symbol OR --image, not both.
# Wallpaper gallery: PosterBoard dies in -[PBFHomeScreenPreviewImageBuilder _imageForView:] on a nil dictionary value,
# PhotosPosterProvider asserts in -[NUDevice_iOS initWithName:]. Which call returns the nil, and what the snapshot path uses.
c="$1"
mkdir -p probe
step() { echo; echo "===== $*"; }

step "ipsw version"; ipsw version

for sym in \
	"-[PBFHomeScreenPreviewImageBuilder _imageForView:]" \
	"-[NUDevice_iOS initWithName:]" \
	"_NUMetalDeviceCreate" ; do
	step "disass $sym"
	ipsw dyld disass "$c" --symbol "$sym" 2>&1 | head -500 | tee -a probe/disass.txt
done

step "PosterBoard: snapshot and render methods"
ipsw dyld macho "$c" PosterBoard --objc 2>&1 | grep -E '^@interface|napshot|Render|IOSurface' | head -400 | tee probe/posterboard.txt
step "PosterKit: snapshot and render methods"
ipsw dyld macho "$c" PosterKit --objc 2>&1 | grep -E '^@interface|napshot|Render|IOSurface' | head -500 | tee probe/posterkit.txt
step "done"
