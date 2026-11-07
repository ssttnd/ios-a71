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

Owner's report on bundle 6c13b5e, image ab0fed23, for next session: blur still pixelated, so the NotMipmapped mapping did not fix it and that explanation is unconfirmed. Screen fade OK. Widgets all white. No wallpaper; the picker cannot take an image from the gallery although screenshots are there. Hardly any hangs. Screen tearing present. Unknown whether the phone rebooted, so whether the LaunchServices job ran: check logs/usb-ls1.txt for `sweet-wallpaper` and for a second boot.

**2026-11-07 big push, owner allowed everything, experimental firmware, boot arg switches. Battery was at 3-5%: a one second torch test at 3% cut the phone off (TWRP, reads only, no harm seen). Do not test the torch on low charge.**
Facts read in TWRP: RTC keeps running, since_epoch + 1778420495 was the real time, so the offset is stable. Gauge s2mu106 at I2C SDA 101 SCL 102 addr 0x3b; regs checked against the driver (see bsd/dev/sweet_a71.c). Charger 0x3d and flash on SDA 42 SCL 43; 0x74 does not answer a plain read; torch-gpio 24, flash-gpio 22 (TLMM SOUTH). `backup/phone-root-now.img` is a copy of the root (ignored). Firmware in research/a71-firmware/{modem,apnhlos} (ignored); bluetooth partition did not mount as vfat, crbtfw/crnv not found yet.
Crash reports: PosterBoard SIGTRAP in `_stateLock_setupSwitcherConfigurationForFirstTime`, as the fork saw before MACs, AMFI and LS rebuild. LS job had not run: no second boot. Removable apps sit in `/private/var/staged_system_apps`.
Patches: 0195 (sweetbattery=2, sweetrtcoff, sweetpowerhold ms default 3500, sweetgpudelay, fade 10 steps), 0196 (package op mv), 0197 (sweetswapwait=1: SwapWait waits for the copy; backboardd does call it; meant to replace sweetpresentlock). Bundle d726894: passes reading a mipped texture sample linearly, for the blocks; earlier explanation unconfirmed. Wi-Fi: userspace/wifi patches with A71 regions, CI sweet-wifi 42348346898, tools/adt-add-wlan.py, SWEETWLAN=1; `build/next.payload` holds bundle, wland, netsvc, modem and wlan firmware, rmtfs copies, launchd jobs (rebuild, wland), network service, time zone Asia/Qyzylorda, 32 app moves. wland off: boot arg sweetwifi=0. Originals: build/ls/*.orig, research/wallpaper/root.
Background job `night1.py` flashes when CI passes and charge >= 15%, tag wave1 (log logs/usb-wave1.txt).

Results on wave1 (kernel fdaf1c2, sweetgpudelay=15, logs/usb-wave1.txt, root copy backup/phone-root-now.img): GPU on from the start, RTC real date logged, 32 apps moved, power-off hold seen unknown. MODEM BOOTS under Samsung TZ: wland reached WLFW, chip 0x140 family 0x4002 board 0, fw WLAN.HL.3.0.1-00719; stopped at missing bdwlan.b00 (fork: copy bdwlan.bin). LS rebuild returned 1, collections=1 legacy=1 (EXQuery 0), wallpaper appeared after a reboot, widgets partly. Owner: lock screen swipe up does not open; opened once through search then an app; phone froze with no panic in the log. Guess: the first SpringBoard ignores that gesture, as the fork saw, and the 60 s GPU start hid it by restarting it; try sweetgpudelay=60. Residual blur pixels: air_run notes bicubic sampled as linear, check that next. Charger registers iOS vs Android differ at 0x1b (10 vs 1b) and 0x22 (aa vs fa); nothing written yet. 0199: battery formula, torch on both volume keys. build/p2.payload: bdwlan.b00, launchd without the finished rebuild job. WSL: approved files deleted, IPSW and dsc in backup/wsl-salvage (hash checked); WSL boot.img differed from the Windows one and was kept.

**Next:** flash 0199 with p2.payload and sweetgpudelay=60; A/B `sweetswapwait=1 sweetpresentfence=1` without sweetpresentlock; torch at safe charge (dump registers around rear_torch_flash with `i2cdump -fy`); charger registers logged by sweetbattery=2 vs Android; tearing; Bluetooth firmware; audio; camera last; remove debug args. Move the GPU stage earlier so the restart is not seen. Later: widgets (retest sandbox patches), time, Wi-Fi per
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

**2026-11-07 wave2 boot loop.** Kernel 764c7e7, image 5383318e, args with sweetpmem=1 sweetsmmuwlan=1 sweetgpudelay=60 plus package p2 (bdwlan.b00). Observed in logs/usb-wave2.txt: package applied 6/6; phone restarts with no panic text, about 30 s after wland's smc 42000205 and its 16 KiB PMEM allocations, before backboardd connects to the GPU. Wave1 had the same kernel path without bdwlan.b00 and did not restart. Guess, not confirmed: Wi-Fi firmware start after the board file resets the phone (compare MSA size note in docs/a71-radio-port.md). wland.log on the root not read yet. Recovery image 9a6db090: same kernel, without sweetpmem, sweetsmmuwlan, SWEETWLAN and sweetpayload; job scratchpad twrp1.py waits for TWRP, copies the bluetooth partition to backup/a71-bluetooth-part.img (read only), flashes, log logs/usb-wave2b.txt. Local, unpushed: 0200, 0201 (sweetcharge=MA, cable state from CHG_STATUS0), tools/adt-add-bluetooth.py, userspace/bt, bt.yml, SWEETBT in package_gpu_boot.py. None of these built or run.

Recovery image 9a6db090 flashed 13:10 (VERIFY step output lost, boot args in logs/usb-wave2b.txt confirm it). The A71 `bluetooth` partition is all zeros: no BT firmware there. crbtfw/crnv not in NON-HLOS either; next place to read is /vendor/firmware via TWRP, or super.img of the stock AP. tools/a71-wave3-package.py builds the next launchd package (vmd, ReportCrash, axassetsd removed, bluetoothd transport, BlueTool stub); dry run only.

Second stop on image 9a6db090 (logs/usb-wave2b.txt): home screen appeared, then the GPU was lost. Observed: third AegirPoster open has no connected or refused line, then backboardd submit failed -16, later backboardd sig 11, GPU LOST, then "no msm device". From the code, not traced at runtime: setScope and close test base 0 on a fresh client, so a frame in flight fails the open and sets backend.lost silently. Fix 0202. Wave2 restarts are separate: Wi-Fi stays off until wland.log is read.

wland.log and wland.log.prev read from the phone in TWRP over adb (block reads, journal ignored, so they may lag): both end at the modem's tftp read of wlanmdsp.mbn, rsize=3813376, no WLFW lines. Which boots they belong to and how much buffered text the reset lost are unknown, so this does not settle the wave2 reset. The USB logger did not capture the owner's second boot on 9a6db090. The screen off about 10 s after start is unconfirmed on that boot; on the captured boot it follows the GPU loss fixed by 0202. Waiting: CI of 5190a7f, then chain3/night2 flashes wave3 (log logs/usb-wave3.txt).

**Wave3 result (kernel 5190a7f, owner):** stable, no resets, GPU not lost (0202 held), apps there, wallpaper with motion, multitouch, battery percent right, charge icon appears but stays after unplugging. Not working: Wi-Fi (off), torch (kernel log says written, no light), widgets, Apple Music crashes, Find My white screen, Settings hung a few times, wallpaper picker thumbnails empty, wrong time and date (kernel calendar is right, time zone was Pacific, guess). Blur still pixelated. Next: Wi-Fi first. wland 0003 logs to USB (sweetnull=backboardd,wland) and uses /var/local/wland.try so a restart costs one boot; wave4 = kernel 5190a7f + that wland + localtime link to Asia/Qyzylorda (PC zone, owner to confirm). 0203 (torch GPIO, charger status log, charge mode) pushed in b04daf6, not flashed yet. After a replug the log showed 0x18 mode 1 and 0x1f 08.

**Wave5 (kernel b04daf6, wland 13db495 with pool at 0xF0000000):** same restart right after "ce: 12 rings programmed" and "bridge: /dev/wlan open". Observed: no "SWEET SMMU" line in the kernel log at all; sweet_smmu_wlan_stream() (0153) runs only inside the probe thread that needs sweetsmmuprobe=1, which was never on the A71 line, while wland skips its own SMMU step because of sweetsmmuwlan=1. So the WLAN stream 0x240 had no SMR when the firmware started DMA. Likely cause of the restarts (inferred, test pending): wave6 = same kernel + sweetsmmuprobe=1, package only removes wland.try. The pool move stays (camera_mem_region covers 0xE0000000 on A71).

**Wave6 (kernel b04daf6 + sweetsmmuprobe=1, wland 13db495): Wi-Fi up, no restart.** Kernel log: "SWEET SMMU: WLAN 0x240/0x1 -> CB4 via SMR6 ... ok". wland: FW READY, WMI READY, HTT 3.71, passive scans find 4 networks every ~15 s, en0 made. Owner: networks show in Settings, tapping one hangs ~8 s before the password sheet, Join does nothing. Log: bridge join (PMK) -> radio join -> "vdev start resp: status -1" every time. wland eeprom regdomain is 0 (board file bdwlan.b00 = Samsung bdwlan.bin). Fix in test: wland 0005 cycles regdomain pairs 0x10, 0x37, 0x64, 0x6a, 0x60 after each failed start (guess that regdomain is the cause). sweetpowersave dropped from the line to fit sweetsmmuprobe (607 bytes).

**Wave7 (wland c7e3304 with regdomain cycling): boot loop.** logs/usb-wave7.txt has only the first boot up to rmtfs; USB events flooded (0xc046) and the logger stopped, so the loop boots were not logged. In that boot the old wland (0004) started at 1146 and the package replaced the binary and removed wland.try while it ran. Not known: where the later boots reset. Guess: iOS saved 'Damir' after the owner's join attempt and joins at boot; the new wland then switches regdomain after a failed start. On TWRP the root showed no wland.try (journal ignored). Recovery image 08336e21 (kernel b04daf6, no sweetpmem, Wi-Fi off) flashed 14:2x, log logs/usb-wave7b.txt. Lesson: start the USB logger before a Wi-Fi test and keep it out of tool calls that can be interrupted.

**Wave8 (kernel b04daf6 + SMMU, wland c7e3304, logger started right after the flash):** no restart, no auto-join at boot. Owner pressed Join about 10 times: every vdev start -1 with regdomains 0, 0x10, 0x37, 0x64, 0x6a, 0x60 (regdomain try up to 16). So the regdomain guess was wrong. The wave7 loop stays unexplained. Next guess: MSA size; wland 0006 gives the whole 1.5 MB carve-out (was 1 MB); the firmware's MSA regions were 9a700000+4000 secure and 9a704000+fc000.

**Wave10 (sweetwlanretry=1, wland 53c70a8 with 1.5 MB MSA):** firmware MSA region 1 grew to 9a704000+17c000, scans fine, joins still vdev start -1. Guard flaw: going to TWRP within 5 min leaves wland.try and the next boot skips Wi-Fi. wland 0007: raw start response and 8 variants (pre-start commands, phymode, ALLOW_HT).

**Bold Text (owner, 2026-11-07):** turning it on made SpringBoard crash at every start (sig 11, pc 0, logs/usb-wave11.txt, usb-boldfix.txt), black screen after the throbber. Removing com.apple.Accessibility.plist by package did not hold (the file was back with EnhancedTextLegibilityEnabled=1; guess: cfprefsd wrote its cached copy). Package boldfix2 put a folder with that name in place (README inside); next boot SpringBoard stayed up (logs/usb-boldfix2.txt). Side effect: Accessibility settings do not save until the folder is removed. Why bold crashes SpringBoard is not known. From TWRP, `adb reboot` returns to recovery; use `setprop sys.powerctl reboot` (tools/a71-flash-boot.py does).

**Wi-Fi join, wave11 wland 6a1b30a:** all 8 variants (pre-start commands, 11G/11NG_HT20, ALLOW_HT) got the same answer, raw tag 0x28 len 36: vdev 0, req 0, type 0, status 0xffffffff, rest 0, within 4-150 ms. Our 72-byte fixed_param matches qcacld's wmi_vdev_start_request_cmd_fixed_param up to disable_hw_ack (newer firmware adds preferred streams, he_ops, cac, regdomain). No dbglog events arrive. Open: what this HL3.0.1 firmware wants before vdev start; next look at qcacld wma_vdev_start / send_vdev_start_cmd_tlv and its pre-start sequence for a STA.

**Wi-Fi source match found:** github.com/R0Xofficial/android_kernel_samsung_a71 is Samsung's A715FXXSBDXB1 kernel with qcacld-3.0, fw-api and qca-wifi-host-cmn under drivers/net/wireless/qualcomm/wcn39xx (downloads kept in scratchpad sam/). Compare wland's WMI against it before guessing. First finding: qcacld's vdev create carries num_cfg_txrx_streams and two wmi_vdev_txrx_streams (tag 0x19b); ours did not, and the start response had chain_mask 0. wland 0008 adds them (not yet run).

**Wave12 (wland 86552d3, vdev create with streams):** "vdev create: 2 tx/rx streams per band -> 0", scans fine, vdev start still -1, chain_mask 0. Next: wland 0009, vdev start fixed param 104 bytes as qcacld (preferred_tx/rx_streams = rf chains, LDPC_RX flag). Not run yet.

**Wave13 (wland 00ea6c9, qcacld-length vdev start with preferred streams):** still -1, chain_mask 0. wland skips SERVICE_READY_EXT (qcacld waits for it and puts hw_mode into INIT) and logged no unknown events. wland 0010 logs every unhandled WMI event, to see ready-ext or error events.

**Wave14 (wland cb2f3c6, unhandled events logged):** firmware sends 0x4009 SERVICE_READY_EXT (924 bytes) right after our INIT, 0x4022, 0x3a001 (country "US"), many 0x4002 channel info during scans; nothing after vdev start but the -1. qcacld sends INIT after the ext event and skips hw_mode for single-MAC chips. Samsung's icnss_qmi.c sends no BDF_DOWNLOAD at all: wland 0011 stops sending one (we sent bdwlan.bin renamed b00); the package puts all 33 stock bdwlan.* into /var/local/fw/modem in case the firmware asks over tftp.

**Wave15 (wland 89eb753, no host BDF):** the modem went ERR-FATAL right after cal report, the GPU then refused submits (busy) and gestures died. So this firmware needs the host's board data; package bdfhost (/var/local/wlan-bdf-host) restored it, phone fine again. **Board file sweep (wland 0012):** with /var/local/wlan-bdf-sweep (index) and wlan-steps.conf ff (probe: vdev start without scan), each boot sends the next of the 34 bdwlan.* files, logs "SWEEP RESULT: file -> status, chain_mask" and restarts the phone after a failure; "SWEEP FOUND" ends it. Log: logs/usb-wave16.txt.

**Board file sweep result (logs/usb-wave17.txt, stopped after 16 of 34 by choice):** every file gave vdev start -1, chain_mask 0. PSCI SYSTEM_RESET from wland restarts cleanly; reboot(2) panics. Some boots after a reset restart again during bring-up (wasted boots), cause unknown. So the board file is not the cause. Next: wland 0014, INIT after SERVICE_READY_EXT (0x4009) with Samsung's 75-word resource config incl. num_max_sta_vdevs = 2; the package ends the sweep (index 99, steps 7f).

**Wave18/19:** wland 315ad10 (INIT after SERVICE_READY_EXT, 75-word config) still vdev start -1 (owner's joins at the end of logs/usb-wave18.txt; I first misread the log as having no joins). Gestures: lock screen swipe works with sweetm2restart=2 (SpringBoard restarted with backboardd when the GPU comes up); line has no sweetwlanretry now. **Stock vendor (dm-1, mounted ro in TWRP):** /vendor/firmware/wlan/qca_cld/bdwlan.bin differs from apnhlos bdwlan.bin (8100970a vs a82ac819); also WCNSS_qcom_cfg.ini (etc/wifi and qca_cld), regdb.bin, bin/cnss-daemon, copied to research/a71-firmware/vendor-wifi (ignored). Package vbdf puts that vendor bdwlan.bin as bdwlan.b00 (test pending).

**Vendor board file (wave19):** with /vendor/firmware/wlan/qca_cld/bdwlan.bin the firmware reports rf chains 1 (was 2), SERVICE_READY 320 bytes, READY_EXT 436, then 0x4022 SERVICE_READY_EXT2 and 0x3a001 REG_CHAN_LIST_CC ("US"); vdev start still -1. Owner's join confirmed in the log. Strings of /vendor/bin/cnss-daemon show it downloads regdb.bin ("Failed to Download the REGDB File"), cal files /data/vendor/wifi/wlfw_cal_%02d.bin, MAC via DMS, and the BDF. wland 0015 sends regdb.bin (bdf_type 2) before the BDF; package carries vendor bdwlan.bin as b00 and regdb.bin.

**Wave21 (wland 50fa6b9: regdb.bin downloaded, vendor bdwlan.bin, guard off; line has sweetm2restart=2, GPU and gestures fine):** regdb download -> 0, BDF sent, rf chains 1, INIT after READY_EXT; joins still vdev start -1, chain_mask 0. Firmware indications seen: MSA READY, PIN_CONNECT_RESULT (0x2c), FW READY; no cal download request. Ruled out so far: regdomain, MSA size, vdev start layout, vdev create streams, 16 apnhlos board files, vendor board file, INIT timing and size, regdb. Open leads: SERVICE_READY_EXT2 (0x4022) before INIT, REG_CHAN_LIST_CC handling, cal data (cnss-daemon's /data/vendor/wifi/wlfw_cal_*.bin, gone with Android's data), WCNSS_qcom_cfg.ini settings, DMS MAC. No mainline Linux for Samsung SM7150 found on GitHub.

**Wave22 batch (owner asked for every open lead in one build):** 0018 INIT chain masks = (1 << rf chains) - 1 (stock WCNSS_qcom_cfg.ini has gSetTx/RxChainmask1x1=1; we sent 0x7) and INIT after SERVICE_READY_EXT2; 0019 logs REG_CHAN_LIST_CC (country, rules). Calibration mode left out: icnss never uses it and the firmware never asked for cal data.

**Wave22 result:** new wland ran in the second boot: regdb -> 0, SERVICE_READY_EXT2 (12 bytes), INIT chain masks 0x1, regulatory US with 2402-2472 MHz allowed; joins still vdev start -1. Full-sequence comparison with qcacld started: its station join creates the BSS peer (wma_add_bss_peer_sta) before vdev start; wland did it after. wland 0020 moves PEER_CREATE before vdev start. r/hackios is not readable from here (reddit blocked); sunfish and sweet use the same wland with firmware HL3.2.x, ours is Samsung HL3.0.1.

**rmtfs buffer:** wland's rmtfs uses RMTFS_PA 0xE1000000 (buffer e1001000), also inside camera_mem_region. Wave23's second boot reset during rmtfs work right after "rmtfs: alloc ... -> e1001000", and the earlier "wasted boots" in the sweep look the same (guess, matches timing). wland 0021 moves it to 0xF2000000.

**2026-11-07 19:3x WI-FI WORKS on the A71 (wave23, wland 2f9d25e, kernel b04daf6, line sweetamfi sweetpayload sweetpmem sweetsmmuwlan sweetsmmuprobe sweetm2restart=2).** Owner joined 'Damir' from Settings. Log (logs/usb-wave23.txt, third boot): peer create before vdev start -> 0; vdev start status 0 chain_mask 0x1; auth 0, assoc 0 aid 8; WPA2 keys; joined; configd en0 192.168.0.192; ~1100 packets each way. Cause of the long vdev start -1: Samsung's HL3.0.1 firmware needs the BSS peer before vdev start (qcacld order); the donor wland made it after. Also in place and matching qcacld: SMMU stream (sweetsmmuprobe=1), pool and rmtfs out of camera_mem_region, MSA 1.5 MB, vendor bdwlan.bin, regdb.bin, INIT after EXT2 with chain mask 1, vdev create streams, 104-byte vdev start. Which of these were needed besides the peer order is not tested. Pending: wland 0021 (rmtfs at 0xF2000000) queued as wave24 for the next TWRP visit; 8 s hang when tapping a network; sweetpowersave and sweetcharge back on the line; clean up the variant/regdomain cycling code.

**Wi-Fi drop (wave23):** about 2 minutes after the join (tx 8346 / rx 11178 at 120 s) wland printed nothing more, no crash line: the process hung, Wi-Fi gone. Guess: a /dev/pmem read of the rmtfs buffer at 0xE1001000 inside camera_mem_region stalls the CPU (same class as the reset boots). Fix 0021 moves rmtfs; 0022 adds a watchdog that prints the main thread's pc/lr/frames after 15 s without poll progress.

**Wave24 (rmtfs at f2001000, watchdog):** joined again, ~8000 tx / 13000 rx frames in 40 s, then at ~125 s the kernel logged "flame wlan: the radio's side closed /dev/wlan" and "net: the link (wcn3990) is down": wland had ended, not hung (no watchdog line, no crash report, no radio_summary). net() has no exit path, so a signal ended it; SIGPIPE fits (busy link, socket/pipe writes). wland 0023 ignores SIGPIPE, names other fatal signals, logs resident memory every 30 s.

**Wave25 (SIGPIPE ignored, signals and memory logged):** wland ended again ~131 s into its run (55 s after the join), resident memory 2.7-3.4 MB, no caught signal, no crash line: SIGKILL (the kernel's crash line only covers corpse exits). wland 0024 forks a watching parent that prints how the radio process ended (signal, max RSS, CPU) and sets jetsam priority 190 for the radio process. Kernel side idea for the next kernel batch: log every SIGKILL with its sender.

**Wave26 (watching parent, jetsam 190):** radio process printed "wland: ended by signal 15" ~90 s in (20 s after the join); the parent printed nothing, so the whole job got SIGTERM (launchd-style). No kill code in wland/netsvc. wland 0025 logs the SIGTERM sender pid (SA_SIGINFO) in both processes and survives it; the radio process calls setsid().

**Wave27 (wland 3e389db: SIGTERM caught and logged, radio process setsid): link held 6+ minutes** (tx 17128 / rx 31465 frames at 391 s, few failures) and no SIGTERM arrived at all, so the earlier SIGTERM most likely targeted the process group. Sender still unknown. First boot after the TWRP flash hung during rmtfs with "modem ready after 100 ms" (modem still running from the warm restart); a hard reset booted fine. Open: shut the modem down properly when it is already up (PAS shutdown, MSA back) before booting it; RSSI_BOUNDS and RSSI updates (kernel batch); 8 s hang on tapping a network; remove the regdomain/variant cycling; kernel batch also restores sweetpowersave and sweetcharge.

**Cause of the reset/hung boots during modem bring-up (found 2026-11-07 20:1x):** rmtfs.c keeps one hyp_assign'd buffer per boot and recognises "this boot" by kern.boottime, which here is a small repeating number (35-50 s; the RTC is set later). When it matched the saved one, rmtfs logged "buffer ... from this boot's first run", skipped hyp_assign, and the phone reset or hung right after the modem's first rmtfs alloc. Every such boot in usb-wave17/23/27 failed; every other boot went on. wland 0026 uses kern.bootsessionuuid (new every boot). The boot stamp in wland.c had the same flaw (already off by 0004).

**Kernel batch 0204 (not built before this note):** PSCI SYSTEM_OFF/RESET in PEHaltRestartInternal (shutdown/restart from Settings panicked); Wi-Fi scan results and link events carry noise -95 and snr (status bar had no bars with noise unknown); flame_sta refreshes the joined BSS's RSSI from background scans; defaults: sweetgpudelay 15 s (was 60), sweetpowersave on, sweetcharge 1000 mA, so the line drops those. App Store: no App Store process crashed in usb-wave27 (not opened); needs a try on the new build.

**Owner report on kernel a580cdd (wave29), mostly not logged (USB log ended 20:40):** Apple ID sign-in "MID is invalid"; Wi-Fi only after a second boot (first boot after a flash: launchd started wland between the package's rm and mv, so no wland); still no Wi-Fi bars; most Apple apps load forever; Music crashes; Settings > Screen Time hangs Settings; wallpapers blank, choosing Color/Photo hangs or crashes (Color applied); SpringBoard crashed in Safari and then nothing opened; auto-lock does not work; Wi-Fi does not auto-join known networks. Logged: AegirPoster crashed 46 times in one boot. Kernel 0205: privileges granted by entitlement (hakios kern_minimac table) for the 10001/10003/1002/1005 refusals of nsurlsessiond, trustd, cloudd, mDNSResponder, geod, WebKit networking, symptomsd, dasd. MID: AuthKit's machine ID comes from Apple's device provisioning (FairPlay/ADI); likely out of reach on a phone with synthetic identity.
