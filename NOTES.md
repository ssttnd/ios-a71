## A71 handoff 2026-11-06 (READ FIRST): lock screen reached

**Observed (owner, on screen):** after writing root v13 and a plain system reboot:
verbose text, Apple logo with NO progress bar, then the iOS lock screen. No log has
been pulled from this boot yet; touch/home/apps not yet reported on this root.

**Cause of the old "bar stuck near 99%":** the root had been built by `mkiosroot.sh`
but `sweet-root-tweaks.py` was never run on it (read directly from the image). Log of
that boot (flush 517): datamigrator ran; SpringBoard main thread in all 40 samples at
`FBSystemShellInitialize -> -[BKSSystemShellService _checkIn]` (sync XPC to backboardd);
backboardd idle in its run loop; CommCenter crash loop. Link between migration and the
checkIn wait is inferred, supported by the fix working, not stated by any log line.

**On the phone:** `userdata` first 16 GiB = v13, SHA-256 `c6660c54...cd7b81` verified by
full readback. `boot` unchanged, SHA-256 `9caef9cc...ae1c` (known working; no local
build has this hash). Its args: `cpus=8 sweetfb_hz=60 sweettouchdelay=3
sweettouchbitbang=1 sweetusb=2 sweetfb_noconsole=1` plus the usual root args.

**Local (gitignored):** `backup/2026-11-06/` boot, recovery, dtbo, vbmeta*, persist, efs,
sec_efs, modemst1/2, fsg, fsc, param + `SHA256-phone.txt` (contains IMEI-class data, never
commit). `backup/ios16-root-20H392-a71-v13.img` clean restore image. `dtb/boot_unpacked`,
`shim/out/Image`, `build/a71_highmem.adt`, `research/ipsw-x/d22ap.adt` copied from WSL.
Stock firmware matching the bootloader (A715FXXSBDXB1):
`Desktop\misc\misc\71stuff\ROMS\Stock`. adb: `Desktop\misc\misc\71stuff\Minimal ADB and Fastboot`.

**Owner rule: do NOT rely on WSL.** Windows folder is the only working copy. WSL `~/ios-a71`
and unneeded Redmi leftovers get deleted later, only on the owner's request. Still only in WSL:
`research/dsc` (shared cache for symbolication, re-extractable from the IPSW), `gh` login.
Editing the HFS+ root currently has no Windows tool (read-only: `tools/hfsread.py`).

**A71 facts learned:**
- No `fastboot boot`. iOS boots from the `boot` partition. Logs via TWRP.
- From TWRP use `adb shell setprop sys.powerctl reboot`. Plain `adb reboot` gives reason
  `reboot,shell` and the bootloader returns to recovery (`androidboot.boot_recovery=1`).
- TWRP shell arithmetic is 32-bit: never compute userdata offsets on the phone
  (`tools/pull_log.py` does it in Python). TWRP `dd` reading a long nc stream failed at about
  15 GiB with `read error: Bad address`: use `tools/a71-write-root.py` (verified 1 GiB chunks).
- USB is off in iOS: `SWEET USB: controller unpowered or unclocked; DWC3 not read`
  (Samsung ABL leaves GDSC/clocks off, unlike Xiaomi fastboot). So `sweetusb=2` does nothing;
  Windows also likely lacks an inbox CDC-ECM driver, so CDC-ACM serial is the suggested transport.
- Current 0085 log prints `Adreno 618 Core Active!` while GX GDSC timed out and the CP_RB_BASE
  readback is 0: the GPU is NOT working despite that line.
- Hardware per device tree: WCN3990, WCD937x + TAS2562, s2mu106/107, PN547.

**GPU/Wi-Fi/BT lead (not yet tried on A71):** https://github.com/DC28088/ios-sweet branch
`codex/native-adreno-metal` (head 67448d2151, fork of dipa-studio) claims Metal on A618 via
AIR -> NIR -> Mesa freedreno/ir3, Wi-Fi and Bluetooth on the same SoC, patches 0081-0183.
Claims are that author's, on Redmi. A71 needs: own signed `a615_zap`, OPP table for 730,
Wi-Fi/BT rails, MSA/MPSS addresses and BDF from A71 DT, panel and battery and haptic work.
Patch numbers collide with ours from 0081. Main unknown: Samsung TrustZone behaviour.

**Owner's test of v13 (same day):** home screen, Settings and touch work. Very laggy, black
wallpaper, many apps not working, occasional strong artifacts. Log (147 flushes): Preferences,
MobileSlideShow, MobileSMS, MobileSafari, AppStore, MobilePhone, Camera were launched and none has
a CRASH line; only assetsd (26), mediaserverd (1), driverkitd (1) crash. So the app failures look
like hangs (not confirmed). Artifact cause not confirmed; tearing suspected. Wallpaper: the fork
needed provider registration and HEIC to PNG, and its auto-registration once blocked boot: deferred.

**Boot of 0086-0089 (CI 42250744002, 130bb90, Intel runner; boot image SHA-256 3b5825ad..., on the
phone now). Measured on this A71 from the persistent log:**
- A76 domain was at 300 MHz (perf_state 0, measured 299989 kHz); now 2208 MHz (measured 2207890).
  A55 domain was 1497.6 MHz; now 1804.8 MHz (measured 1803347). Domains had dcvs=0.
- DDR votes MC0/SH0/ACV all returned 0.
- USB WORKS: first try. Windows enumerated CDC-ACM and `tools/a71-usblog.py` captured the console
  live (337 KB in `logs/usb-first.txt`, matching the ring). DSTS speed 0 = high speed. The SMMU
  already had SMR3 for stream 0x540, so no entry was added. PLL ready. No panic.
- Owner's view of speed on this boot: not recorded yet. `ue0 rx/tx` lines in ACM mode are cosmetic.
- CI: arm64 macos-15 had no capacity on 2026-11-06; macos-15-intel with Xcode 16.0 builds a kernel
  that boots, so the old "Xcode 16 iig is incompatible" note no longer holds for this tree.

**Patches 0086-0089 (owner approved doing all four):**
- 0086 cpufreq-hw (fork 0126): the fork measured 300 MHz on Redmi's A76s; not measured on A71 yet.
  The log lines `SWEET CPUFREQ` will show it. Domain addresses checked against the A71 live DT.
- 0087 UI threads to A76 (fork 0167), changed to exclude kernel threads, which the fork fixed later.
- 0088 DDR bandwidth vote (fork 0169) plus a BCM vote helper. Off with `sweetddr=0`.
- 0089 `sweetusb=3`: late thread powers up rails (L4A/L11A/L17A), SN15/CN0 votes, TBU2 and USB
  GDSCs, RCGs, clocks, SMMU stream 0x540, wrapper, QUSB2 from the DT sequence, core reset, then the
  gadget as CDC-ACM (1209:0002) streaming the console. Every step logs first, so the last log line
  shows a stall. `sweetusbsmmu=0` skips the SMMU step. Unknowns: Samsung MUIC/CCIC D+/D- routing,
  bus votes for DMA. First-try enumeration is not expected to be certain.
- Host: `tools/a71-usblog.py` (no packages; finds the port by USB ID; written, untested on hardware).

**Local patch workflow (Windows):** `research/buildtree.tgz` is the CI tree of 9b05f9c;
`research/xnu-tree` holds extracted files patched through 0085 (no compiler here: compile errors
show only in CI). Patches were checked with GNU patch `--fuzz=0` against a copy of that tree.

**Packaging check:** `tools/package_gpu_boot.py` with the old kernel and `SWEETUSB=2` reproduces the
phone's boot image byte for byte over its 17223680 bytes, except the 64-byte ADT `random-seed`.
Output is now `shim/out/shim-boot.img`. Kernel zip: newest `Downloads\xnu-kernel*.zip`.

**Porting plan for GPU/Wi-Fi/BT/audio:** `docs/a71-radio-port.md`. Best donor found:
Yofuness/ios-flame (Pixel 4a, same SD730G/A618). CI moved to macos-15 (85c1c27), since GitHub
retired macos-14; run 42241740972 was queued when this was written. Pushing now works from Windows.

**2026-11-07 state, branch `a71-gpu` (55c5ede):** fork DC28088 0081-0183 applied cleanly on our 0080
base (built locally in `research/tree-0080` by reversing 0085..0081 from the CI tree), A71 touch and
USB console on top as 0184-0188. GPU bring-up is staged by boot arg: sweetgpu=1 read-only, 2 ARC read,
3 gfx.lvl, 4 CX power, 5 GMU clocks, 6 firmware staging, 7 DMA/SMMU/HFI, 8 GMU/HFI start, 9 SQE/CP. Stages
6+ need the A71's own firmware in the ADT (`tools/sweet-adreno-firmware.py` in the fork; its zap sizes are
Redmi's). Metal also needs a userspace bundle on the root.
**Unsolved boot loop:** images from main 3075cab, b3e4766, 66077bb looped before any log flush, and
removing the fork's sandbox patches did not help. The last good image is main's 52e492f kernel packaged
with `SWEETNOTIME=1` (boots, reached home screen; on the phone now, hash 3142e516...). 66077bb differs from
it only by 0091/0092 (RTC code not executed without its args, a 15 s display probe) plus ufslogsecs=1. Cause
not known. The fork's sandbox patches were therefore wrongly suspected: retest them.
**Do not run `tools/a71-usblog.py`/`a71-bootcheck.py` unattended before CREATE_NO_WINDOW (now fixed):**
detached runs opened a console window every poll and annoyed the owner.

**2026-11-07 GPU result, kernel 2a8b02c, sweetgpu=9 (log logs/usb-gpu9.txt, gitignored):** image with the phone's own firmware (research/a71-firmware, pulled from /vendor/firmware and apnhlos). Observed: signed zap accepted (phase 5, status 0), legacy GMU boot errno 0, HFI OK, SQE ME_INIT OK, CP fence value 0x618c0de completed, GX measured 267000 kHz at index 1. DCVS to index 6 (800 MHz, gfx vlvl 385, mx vlvl 385) then measured 0 kHz; `NativeAdreno A618 handoff errno=5; no userspace rendering`. iOS stays up. Next try: sweetgpukhz lower (boot arg, no rebuild). A71 DT lists 825/800/650/565/430/355/267/180 MHz. Metal also needs sweetnative=1 and the fork's userspace bundle on the root, which needs write access to the running root.
On a71-gpu the home screen boots but looks as on main: white panels instead of blur.

**2026-11-07 650 MHz, same kernel, args `sweetgpu=9 ufslogsecs=1 sweetgpukhz=650000` (image f22a0d49..., log logs/usb-gpu650.txt, gitignored).** Observed: GX measured 649999 kHz, `NativeAdreno A618 handoff errno=0`, ring adopt ready=1, Metal discovery published, SweetMetal2 selected. No panic in the log. Owner reports "system crashed", many artifacts while scrolling, blur visible. Observed: the only backboardd restart in the log is the kernel's own at the handoff (60 s after the stage starts); backboardd then keeps pid 244. Guess: that restart is what the owner saw as the crash; the time of the crash was not recorded. Observed: `backup/ios16-root-20H392-a71-v13.img` has no Metal bundle at all (SweetMetal2, SweetNativeMetal, NativeAdrenoMetal all missing), and nothing has installed one since, so Metal cannot load. Guess: blur and artifacts come from the CPU path, not the GPU; the running root itself was not read.

**Installing into the root (commit 35f3e99, CI pending):** patch 0191 plus `tools/a71-payload.py`: TWRP writes a package to a 256 MiB raw area at the end of userdata (below the log), the kernel applies it 10 s after root mount when booted with `sweetpayload=1`, logs `SWEET PAYLOAD:` and clears the header. Replaces the fork's TCP file server, which needs USB network Windows lacks. Not tested yet. Writing the package is a userdata write: confirm with the owner first.
**Metal plan:** workflow `native-metal.yml` builds the fork's `native-legacy/SweetMetal2.bundle` (Metal to AIR to NIR to freedreno; ad-hoc signed). Install to `/System/Library/Extensions/SweetMetal2.bundle` with `/private/var/local/sweet-native-env` = `FD_MESA_DEBUG=sysmem` (the fork's last working setting), boot with `sweetgpu=9 sweetgpukhz=650000 sweetmtl=2 sweetgles=0 sweetpayload=1`. Rollback: flash an image with `sweetgpu=1`, Metal is then not published. The fork's first trials crash-looped BackBoard until later fixes; those fixes are in the imported commit 67448d2151.

**2026-11-07 FIRST GPU RENDERING, kernel 35f3e99 (CI xnu 42292392537, sweet-native-metal 42292392530, QEMU pass), image 1158eec4..., log logs/usb-metal3.txt (gitignored).** Args `sweetgpu=9 ufslogsecs=1 sweetgpukhz=650000 sweetmtl=2 sweetgles=0 sweetpayload=1 sweetnull=backboardd` (env SWEETGPU, SWEETNULL, SWEETARGS). Observed: package installed the bundle (11/11, errno 0, hash verified before boot). backboardd loads it, `native renderer FD618`, surfaces now BGRA. `scoped submit` past 1440, `failures=0`, gpu_busy_pct up to 48, RBBM shows SP/RB/TSE busy. No panic. What the screen shows was not yet reported when this was written.
Black screen on the two previous images: backboardd asked for b3a8 surfaces, the bundle accepts only BGRA, so 0 submits while presents continued. Fixed by three things together: `artwork-display-gamut` sRGB in tools/mkadt.py, MobileGestalt placeholder MACs (from the fork), and deleting `/private/var/containers/Shared/SystemGroup/systemgroup.com.apple.mobilegestaltcache/Library/Caches/com.apple.MobileGestalt.plist` from the root (owner approved), because that cache kept P3. sRGB alone did not change the format.
The kernel still restarts backboardd about 60 s after boot, when the GPU stage hands off: the owner sees that as a crash. Bundle messages reach the USB log only with `sweetnull=backboardd`.

**Artifacts under Metal, image 1158eec4:** owner: blur great, performance great, many chaotic artifacts across the screen on any motion. Observed in the log: gate_avg_us=0 acquire_avg_us=0, the kernel copied surfaces without waiting for the GPU or acquiring caches, because `sweetpresentwait=1` was missing. Image ebe01be3 (log logs/usb-metal4.txt) adds it. Observed there: `CPU presentation fence gate enabled`, gate avg 7-9 ms and acquire 0.8 ms in busy windows, 24-31 presents/s while the interface was being used, failures=0, no panic. Whether the artifacts went away: not yet reported.

Owner on ebe01be3: no artifacts until the browser was opened, then they returned. Observed: `method=17 result=0xe00002d5` bursts are job-slot backpressure (SubmitMaxJobs=8) that the transport retries, no loss seen. Guess, not proven: Safari/WebKit surfaces are written by the CPU in another process and the bundle skips the cache clean when the surface seed is unchanged (sweet_drm.cpp:539). Test image aaf51268 adds `sweetimportsync=1` (kernel maintains imported surfaces), log logs/usb-metal5.txt. Next A/B if confirmed: `/var/local/sweet-native-always-clean` through a package, which may cost less.

Owner on aaf51268: artifacts on opening Control Center, then they stopped. Observed in logs/usb-metal5.txt: that window has a burst of busy submits, gate avg 9.6 ms, present max 84 ms, coalesced 17; the earlier browser burst in usb-metal4 also had busy submits. Guess, not proven: under load CoreAnimation reuses a surface while the kernel is still copying it. Test image a3e98a32 adds `sweetpresentlock=1` (GPU drained and submits blocked during the copy), log logs/usb-metal6.txt.
Wallpaper: not started on the phone. `research/wallpaper/root` holds the collections extracted from image v13 (gitignored). Plan: HEIC to PNG plus CAML redirect, ExtensionKit flag, `sweetamfi=1`, LaunchServices rebuild only as the last resort. List the files and confirm before writing them.

Owner on a3e98a32: no artifacts, asks for more FPS. Observed: presents 15-39/s, gpu_busy 43-44% (GPU not the limit), submit ioctl about 2.9 ms each under load and 37% of the render thread, against about 0.2 ms in metal4 without sweetimportsync. Test image 1e9f6005 keeps `sweetpresentwait=1 sweetpresentlock=1` and drops `sweetimportsync=1`, log logs/usb-metal7.txt. Watch whether artifacts return. Next candidate: the lock waits for the whole GPU (gate 3-6 ms per frame); needs a kernel patch to do less.
Bug on a3e98a32: after display off and on the owner reports dead touch. Observed: the kernel still reports touches (11 after the last display on), HID clients are open, presents 0, no panic. So input reaches the kernel and the UI does not respond. The fork saw the same on the lock screen with unknown cause; restarting SpringBoard over SSH cleared it there. Not available here without network. `sweetsample=` can sample a process's stacks if it recurs.

Owner, still on a3e98a32: touch came back by itself, so that hang is a temporary UI stall, cause unknown. Progressive blur shows a black band instead. Observed: no refusal or error from the bundle in logs/usb-metal6.txt. The fork recorded a similar black band and left it unsolved (status bar in the search pull-down, top ~135 px of the blurred texture). Its draw log (`/var/local/sweet-native-drawlog`) arms only if the file appears after the process starts, so it needs a bundle change or a way to touch the file while iOS runs.

Owner on 1e9f6005 (`sweetpresentwait=1 sweetpresentlock=1`, no importsync): "чисто", no artifacts. Keep these. Observed: submit ioctl 1.2-2 ms, 25-35 presents/s under load, gate about 5 ms. Black band is at the top of the screen. Guess: it may be blur over the black wallpaper; not checked.
Kernel 3b14aa8 adds 0192: with `sweetdrawlogkey=1` a volume press writes the draw log trigger; CI pending when this was written.
Wallpaper package prepared, NOT written: `build/wallpaper.payload` (sha256 90c18acb..., 47 entries, 15 MB): 16 PNG and 6 CAML for 375x812 variants made with pillow-heif, flag `/private/var/preferences/FeatureFlags/Domain/ExtensionKit.plist` with enforce_xpc_cache_code_signing off (key confirmed in the system plist, override path is a guess from iOS practice). The 6 CAML files get replaced; originals in `research/wallpaper/root` and the v13 image. Needs `sweetamfi=1 sweetpayload=1` and the owner's confirmation. LaunchServices rebuild stays last resort: the fork stalled on the Apple logo with early registration.

Checked for the band: donor's `setScissorRect` is a no-op, but tools/prepare-sweet-native-metal.py already implements scissor and viewport, so that is not the cause. No "not implemented" message in the logs.
The owner approved writing the wallpaper package, then said stop and asked for the blur first. Package not written.

Wallpaper package written with owner's approval, image 247df26a (kernel 3b14aa8, `sweetpayload=1 sweetamfi=1 sweetdrawlogkey=1` plus the kept args), log logs/usb-wall1.txt: 47/47 entries errno 0, trust cache queries answered. Owner: wallpaper still black. Observed: only SpringBoard and backboardd create surfaces, no poster process does. Guess: providers die or are not registered. Commit d57ff4c adds 0193 (0219+0240 from 3075cab, sweetsbext=0 sweetsbcont=0 turn it off), CI pending; it needs its own boot test because main looped with these patches, and it is also the attempt to fix Photos and widgets. LaunchServices rebuild remains the last resort.

Draw log captured (research/drawlog.txt, gitignored, 16000 draws). Observed chain: 83-84 capture into 676x238 then 704x256, 85-88 `variable_blur_downsample_frag_lph` with render target and source both 704x256, 89 `variable_blur_frag_lph` with textures 704x256 and a 1x366 mask, 90 composite. Observed in source: the driver builds render surfaces with level 0 and never read `[attachment level]`. Guess, strong but not seen working: CoreAnimation renders those four passes into mip levels 1-4, they stayed empty, and the blur sampled black. Commit 929ce9e passes the level to the surface, pass size and clear; CI pending. It also logs `rendering into mip level` once and `lod clamp ... is ignored` once. If that clamp line appears, per-binding clamps need implementing too: the donor ignores them.

**Progressive blur FIXED, bundle 929ce9e (CI 42305677638), image 247df26a, log logs/usb-mip1.txt.** Observed: `rendering into mip level 1 of a 704x256 texture`, no lod clamp message. Owner: "все отлично там". New report: the App Library platter disappears; cause not known.

Regression in 929ce9e: App Library goes black with artifacts. Observed: `the draw failed: colour attachment 1 is 704x256 and attachment 0 is 352x128`; the capture jumps from draw 44 (`Tvrd`) to a new frame, so the command buffer was dropped. Commit a9b9f80 keeps attachment 1 on attachment 0's level when it is the same texture and only fails when an attachment is smaller. Same-texture binding is a guess from identical sizes in the log. Owner also reports earlier artifacts on swiping up on the widgets page, possibly the same drop.

Image b9c4db8b (kernel d57ff4c with 0193, `sweetamfi=1 sweetdrawlogkey=1 sweetpayload=1`) plus bundle a9b9f80, log logs/usb-all1.txt: boots, no boot loop with 0193 on a71-gpu, package 9/9, no draw failure so far. Screen report pending.

Owner on that image: Photos FIXED, App Library no longer disappears. Open: the progressive blur is now pixelated; no wallpaper and none can be added; screen off fades in stages. Observed: poster processes never start and do not crash, so providers are probably missing from LaunchServices (fork's finding). Fade cause seen in code: six full reads of uncached panel memory.
Commit cdf29d4, CI pending: 0194 (24-step fade from a cached copy; volume key draw log now details the 704x256 target), bundle logs sampler states, `userspace/wallpaper` helper built but NOT installed. Installing it means editing the merged launchd.plist and a one-time LaunchServices rebuild: the fork stalled on the Apple logo once with early registration, so confirm with the owner first and use `--rebuild-once` from a delayed job.

Image ab0fed23 (kernel cdf29d4; xnu build OK, qemu-smoke cancelled at QEMU install by the 15 min limit, so not smoke tested) boots on the phone. Draw log with details (research/drawlog3.txt): `Tvrd` constant f1 is 0,1,2,3. Samplers logged: min/mag linear or nearest, all mip 0 (NotMipmapped). Guess from Mesa behaviour: freedreno clamps LOD near 0 for that, so each level came from level 0, hence pixels. Commit 6c13b5e maps NotMipmapped to linear mip; CI pending, install waits for TWRP.
Prepared, NOT installed: `build/ls/launchd.plist` (adds job org.sweet.wallpaper-rebuild, `--rebuild-once`, StartInterval 180, root), `build/ls/launchd.plist.orig`, `build/ls/sweet-wallpaper-register`. Waiting for a clear yes.

Owner confirmed the LaunchServices rebuild. Package `build/all-6c13b5e.payload` (15 entries, sha256 0d854c2b...) with bundle 6c13b5e, launchd.plist and helper, with image ab0fed23, log logs/usb-ls1.txt (a hidden a71-usblog.py keeps appending; no window). launchd reads its plist before the kernel applies a package, so the job only exists after the NEXT reboot, and runs 180 s after that boot. Helper leaves `/private/var/db/sweet-wallpaper-rebuild-attempt.json` and refuses a retry.
Rollback for a hang: write `build/ls/launchd.plist.orig` back as `/System/Library/xpc/launchd.plist` through a package written from TWRP (needs a boot image with `sweetpayload=1`, and the kernel applies it only after root mount; if the phone hangs before that, read the fork's raw write notes first). Rollback for the kernel: `backup/2026-11-06/boot.img` or an earlier test image.

Session stopped 2026-11-07 at the owner's request, to continue next day. Not yet seen: blur on 6c13b5e, fade, any effect of the rebuild. Current args: `sweetgpu=9 sweetnull=backboardd ufslogsecs=1 sweetgpukhz=650000 sweetmtl=2 sweetgles=0 sweetpresentwait=1 sweetpresentlock=1 sweetamfi=1 sweetdrawlogkey=1 sweetpayload=1`. Flow scripts are in `tools/flow/` (pk2 packages an image from a kernel commit, pm builds a bundle package, chain2 waits for CI and installs, waitpay and waitflash write from TWRP). They hold absolute paths of this PC; ghauth reads the stored git credential at run time and stores nothing.

**Next:** read logs/usb-ls1.txt; owner's report on blur and fade; reboot for the LaunchServices job and check wallpaper; remove debug args; shorten the present lock for FPS. Move the GPU stage earlier so the restart is not seen. Later: widgets (retest sandbox patches), time, Wi-Fi per
`docs/a71-radio-port.md`. Restore image for boot: `backup/2026-11-06/boot.img`.
Kernel archive of this build: `research/xnu-kernel-130bb90.zip`.

## A71 touch status (2026-10-23) - CONFIRMED WORKING!
- Touchscreen input on Samsung Galaxy A71 4G (SM-A715F) running Apple iOS 16.7.16 is CONFIRMED FULLY WORKING by the device owner!
- Transport: Software I2C bit-banging on GPIO 6 (SDA) and GPIO 7 (SCL) via 'sweettouchbitbang=1' successfully bypasses the hardware-locked QUPv3 SE7 FIFO (GENI FIFO_IF_DISABLE_RO=1).
- Power rails: PM6150 GPIO 4 (SPMI DVDD 1.8V) + TLMM WEST GPIO 56 (AVDD 3.3V).
- Pins: GPIO 8 (Reset active-low), GPIO 9 (IRQ active-low).
- Identification: Samsung S6SY771 (y771 at I2C address 0x48, hardware ID '0xac 0x37 0x71').

## A71 handoff 2026-10-22 (local investigation)

Owner requested moving touch candidates 0083/0084 to main without a build.
Transfer uses a single `[skip ci]` commit; the test-branch workflow-trigger
change is excluded. Hardware validation of 0084 is still outstanding.

Latest test: owner booted GPIO candidate. Persistent log flush 11, total
204170 bytes: `GPIO I2C idle SDA=0 SCL=0`; all probes timeout. Thus GPIO
bypass was active but did not restore transport. Prior GENI test confirmed
`FIFO_IF_DISABLE_RO=1`. CI 39846695301 succeeded at aa2d994.

Candidate 0084 fixes an independent, source-confirmed bug in inherited 0082:
PMIC GPIO register 0x45 is drive strength, not output value; mode 0 is input.
New code selects output mode and high value using subtype-specific Linux
pinctrl-spmi-gpio semantics, checks AP ownership, SPMI completion/errors and
register readback. Actual handset subtype not yet read; code detects it.
Do not claim this is the confirmed cause of stuck SDA/SCL. Local mocked SPMI
tests compile with warnings-as-errors and ASan/UBSan and pass for LV/MV,
legacy, wrong owner, unsupported subtype and bus error. No flash performed.
Recovery disconnected while reading its debug GPIO dump after mounting
debugfs, then returned; avoid full debugfs GPIO/register dumps on this device.

Update: candidate commit `a9a2513` pushed to `fix/a71-touch-gpio-i2c` using
existing Git authentication at the owner's request. The workflow now includes
this branch in its push trigger so the candidate can build without merging.
Build status and hardware result remain unverified. No partition writes.

Read `docs/a71-touch-2026-10-22.md` first. TWRP confirms y771 at 0x48 and
4095x4095 raw coordinates. Previous XNU log: all I2C probes time out (err=60),
no IC selected. Candidate 0083 adds opt-in GPIO I2C (`sweettouchbitbang=1`),
fixes scaling and short-tap batching, and checks initialization. Protocol and
decoder tests pass locally. No full XNU build or handset test yet. No partition
writes, reboot, CI run or push. GENI/GSI mode is a hypothesis needing a log.

## Older history

Redmi Note 10 Pro (`sweet`) bring-up, hardware facts and debugging: `docs/sweet-history-notes.md`. Search it rather than reading it in full (about 170 KB).
