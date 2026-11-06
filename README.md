# iOS 16 on the Samsung Galaxy A71 4G

Apple's open-source XNU kernel (`xnu-8796`) and the unmodified iOS 16.7.16 userland of the iPhone X
(`iPhone10,3`, build `20H392`), running on the Samsung Galaxy A71 4G (`SM-A715F`, Snapdragon 730 / SM7150).
Experimental bring-up, not a usable phone OS. Apple's security model is mostly off, so do not put anything
personal on it. iOS itself is Apple's and is not distributed here.

Based on [dipa-studio/ios-sweet](https://github.com/dipa-studio/ios-sweet) (Redmi Note 10 Pro, same SoC family).
GPU, Wi-Fi and Bluetooth work is taken from [DC28088/ios-sweet](https://github.com/DC28088/ios-sweet) and
[Yofuness/ios-flame](https://github.com/Yofuness/ios-flame). "sweet" in file and option names comes from that origin.

## State

| Area | State on the A71 |
|---|---|
| Boot to home screen, Settings, Control Center | Works, with rendering on the CPU |
| Touch, buttons | Works |
| CPU frequency, memory vote | Works: A76 cores measured 300 MHz before, 2208 MHz after |
| Kernel log over USB | Works on Windows, as a serial port |
| Wallpaper, widgets, many apps | Not working |
| Clock | Starts in 1969 (the RTC does not hold the date) |
| GPU, Wi-Fi, Bluetooth, sound, cellular, camera | Not working. Porting plan: `docs/a71-radio-port.md` |

The current state and history are in `NOTES.md`.

## How it boots

```
Samsung bootloader (unlocked) -> boot partition: shim -> XNU + generated Apple Device Tree
    -> UFS driver -> HFS+ root in userdata -> launchd -> backboardd, SpringBoard
```

**Flashing erases `userdata`.** Back up the phone first: `persist`, `efs`, `sec_efs`, `modemst1/2`, `fsg`, `fsc`, `boot`.

## Tools (Windows, from TWRP over adb)

| Tool | What |
|---|---|
| `tools/package_gpu_boot.py` | Build the boot image from a kernel archive |
| `tools/a71-flash-boot.py` | Write and verify the boot partition, then reboot into iOS |
| `tools/a71-write-root.py` | Write the iOS root to `userdata` in verified chunks |
| `tools/a71-usblog.py`, `a71-bootcheck.py` | Live kernel log over USB, and a boot check |
| `tools/pull_log.py` | Read the persistent kernel log from TWRP |
| `tools/hfsread.py` | Read files from an HFS+ image |
| `tools/mkiosroot.sh`, `sweet-root-tweaks.py` | Build the iOS root (Linux, needs an HFS+ mount) |

The kernel is built by GitHub Actions (`.github/workflows/xnu.yml`), from Apple's source plus `xnu/patches/` in order.

## From a phone reboot

Plain `adb reboot` from TWRP returns to TWRP. Use `adb shell setprop sys.powerctl reboot`.
To leave iOS, hold Power + Volume Down.

## License

The repository keeps ios-sweet's MIT license; `xnu/patches/` modify Apple's XNU and stay under the APSL 2.0.
