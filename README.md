# iOS 16 on the Samsung Galaxy A71 4G

Apple's open-source XNU kernel (`xnu-8796`) and the unmodified iOS 16.7.16 userland of the iPhone X
(`iPhone10,3`, build `20H392`), running on the Samsung Galaxy A71 4G (`SM-A715F`, Snapdragon 730 / SM7150).
Experimental bring-up, not a usable phone OS. Apple's security model is mostly off, so do not put anything
personal on it. iOS itself is Apple's and is not distributed here: you need your own copy of the iPhone X
16.7.16 root filesystem, and your own phone's firmware files.

Based on [dipa-studio/ios-sweet](https://github.com/dipa-studio/ios-sweet) (Redmi Note 10 Pro, same SoC family).
GPU, Wi-Fi, Bluetooth and audio work is taken from [DC28088/ios-sweet](https://github.com/DC28088/ios-sweet) and
[Yofuness/ios-flame](https://github.com/Yofuness/ios-flame). "sweet" in file and option names comes from that origin.

## State (November 2026)

| Area | State on the A71 |
|---|---|
| Boot, lock screen, home screen, Settings, Control Center | Works |
| GPU | Works: native Metal over freedreno (Adreno 618, 700 MHz). About 40 fps in motion; 60 fps is the current work |
| Touch, buttons | Works |
| Wi-Fi | Works (join from Settings) |
| Sound | Works through the speaker amplifier (TAS2562) |
| Apps | Most open: Notes, Mail, Safari, Photos, Calendar, Clock, Music, News, Podcasts, Wallet, App Store (no store access), Health, Messages, Phone UI |
| Maps | Starts, then aborts in its Metal renderer |
| Wallpaper previews in Settings | Grey |
| Bluetooth, cellular | Not working |
| Camera, FaceTime, Magnifier, Measure | Not working (no camera stack) |
| Kernel log over USB | Works on Windows, as a serial port |

Current state, measurements and open problems: `NOTES.md`. Older history: `docs/sweet-history-notes.md`.

## How it boots

```
Samsung bootloader (unlocked) -> boot partition: shim -> XNU + generated Apple Device Tree
    -> UFS driver -> HFS+ root in userdata -> launchd -> backboardd, SpringBoard
```

The phone boots iOS from its `boot` partition. TWRP stays in `recovery` and is how images are written.

## What you need

- An SM-A715F with an unlocked bootloader and TWRP in `recovery`.
- Windows with Python 3 and adb. No WSL is needed; everything that compiles runs in GitHub Actions.
- A fork of this repository with Actions enabled (macOS runners build the kernel and the userland parts).

## First install

**This erases `userdata`.** Back up the phone first, at least `persist`, `efs`, `sec_efs`, `modemst1`,
`modemst2`, `fsg`, `fsc` and `boot`, and keep the `boot` backup: it is the way back to Android.

1. Build the iOS root (Linux with an HFS+ mount): `tools/mkiosroot.sh`, then `tools/sweet-root-tweaks.py`.
2. Boot TWRP and write the root to `userdata` in verified chunks: `tools/a71-write-root.py`.
3. Build and flash a boot image (next section).

## Building and flashing a boot image

1. **Kernel.** Push a change under `xnu/patches/` (or run the `xnu` workflow by hand). The build takes about
   25 minutes and uploads the artifact `xnu-kernel` (`kernel.development.sweet`). A new push cancels a running build.
2. **Image.** `tools/package_gpu_boot.py <xnu-kernel.zip>` writes `shim/out/shim-boot.img`. The boot line comes
   from environment variables:

   | Variable | Value used now | Meaning |
   |---|---|---|
   | `SWEETGPU` | `9` | GPU bring-up level (9 = native Metal) |
   | `SWEETNULL` | `wland` | daemons whose output goes to the console |
   | `SWEETWLAN` | `1` | add the Wi-Fi node to the device tree |
   | `SWEETBATTERY`, `SWEETBACKLIGHT` | `2`, `1` | battery gauge, backlight |
   | `SWEETKEEPSYMS` | `0` | leave `keepsyms` out (the boot line is short) |
   | `SWEETARGS` | `sweetgpukhz=700000 sweetmtl=2 sweetpresentwait=1 sweetswapwait=3 sweetpresentfence=1 sweetamfi=1 sweetpmem=1 sweetsmmuwlan=1 sweetsmmuprobe=1 sweetm2restart=2 sweetrtc=1 sweetrtcoff=<seconds>` | kernel options, explained in `NOTES.md` |

   The phone's own GPU firmware (`a630_sqe.fw`, `a618_gmu.bin`, `a615_zap.*`) goes in `research/a71-firmware/`
   and is packed into the image; it is never committed.
3. **Flash.** Put the phone into TWRP, then `tools/a71-flash-boot.py <adb.exe> shim/out/shim-boot.img <sha256>`.
   It writes `boot`, reads it back, compares the hash and reboots only after `VERIFY OK`.
   `tools/flow/waitflash.py <sha256> <tag>` does the same unattended: it waits for TWRP, flashes and checks the boot.
4. **Log.** `tools/a71-usblog.py logs/usb-<tag>.txt` records the kernel log over USB; `tools/a71-bootcheck.py`
   checks a boot from it.

To go back to Android, flash the `boot` backup from TWRP.

## Metal driver (userland)

The workflow `native-metal` builds `SweetMetal2.bundle` (Metal to AIR to NIR to freedreno). It is installed to
`/System/Library/Extensions/SweetMetal2.bundle` file by file (see `build.json` in the artifact for the hashes).
New processes load it; restarting `backboardd` (kill it) applies it to the UI.

`/var/local/sweet-native-env` sets Mesa variables for every Metal process, one `NAME=value` per line
(`FD_`, `IR3_`, `MESA_`, `GALLIUM_` only). Current file:

```
FD_MESA_DEBUG=sysmem
MESA_SHADER_CACHE_DIR=/var/tmp/sweet-mesa-cache
```

The shader cache keeps compiled shaders across boots, so screens open without the first-use stall.

## Changes on the phone's root that the image does not carry

- `/System/Library/xpc/launchd.plist` without the `com.apple.fairplayd.H2` job. On this phone fairplayd exits at
  once, and its clients (lockdownd, locationd, Notes, News, Podcasts, Wallet) waited forever for an answer.
  Original kept in `/var/local/apple-orig/launchd.plist`.
- `/usr/libexec/lockdownd` with timeouts on its FairPlay calls (`tools/a71-patch-lockdownd.py`).
  Original kept in `/var/local/apple-orig/lockdownd`.

## Remote debugging over Wi-Fi

`sweetdbg` runs on the phone (TCP 7777, key in `build/sweetdbg.key`, not in git). From Windows:

```
python tools/a71-dbg.py --host <phone-ip> ps | shot out.png | tap X Y | swipe X1 Y1 X2 Y2 MS | key power
python tools/a71-dbg.py --host <phone-ip> get PATH out | put file PATH 755 | kill PID 6 | threads PID
python tools/a71-dbg.py --host <phone-ip> reboot recovery
```

`kill PID 6` prints the crashing thread's backtrace to the kernel log; `threads PID` prints every thread.
Frame rate during motion: `tools/a71-smooth.py logs/usb-<tag>.txt` (needs the USB log running).

## Rebooting

From TWRP, plain `adb reboot` returns to TWRP; use `adb shell setprop sys.powerctl reboot`.
From iOS: `a71-dbg.py reboot recovery`, or hold Power + Volume Down.

## License

The repository keeps ios-sweet's MIT license; `xnu/patches/` modify Apple's XNU and stay under the APSL 2.0.
