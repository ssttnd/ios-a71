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

**Next:** owner reports speed and artifacts. Then USB networking that Windows drives without WSL
(needed for SSH, the donors' install path), then base on the Pixel 4a port per
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
