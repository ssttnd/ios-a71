# A71: porting GPU, Wi-Fi, Bluetooth, audio from the SM7150 ports

Written 2026-11-06. Nothing here has been built or run on the A71. Status claims
below are those projects' own, from their READMEs and notes, on their phones.

## Donors

| Project | Phone | Claims | Notes |
|---|---|---|---|
| [Yofuness/ios-flame](https://github.com/Yofuness/ios-flame) | Pixel 4a, SD730G, A618 | Metal via Turnip, Wi-Fi, BT, speaker, camera, sensors. Settings ~57 fps | Same SoC as the A71. MIT. Best overall donor |
| [DC28088/ios-sweet](https://github.com/DC28088/ios-sweet) branch `codex/native-adreno-metal` | Redmi Note 10 Pro, SD732G, A618 | Native Metal, Wi-Fi, BT. 35-40 fps in some windows | Already adds Wi-Fi on the same DT layout as ours |

## Hardware: A71 values from its live device tree

From `/sys/firmware/fdt` in TWRP (`dtb/a71-live.json`, gitignored). Compared with
Redmi and Pixel 4a values as written in those repositories.

| Item | A71 | Others |
|---|---|---|
| Wi-Fi/BT chip | WCN3990 (`qcom,icnss@18800000`, `/soc/bt_wcn3990`) | same |
| WLAN SMMU stream | SID 0x240 mask 1 | same on Redmi |
| MSA region | `0x9a700000`, size `0x180000`; `qcom,wlan-msa-memory` 0x100000 | Redmi `0x97d00000`, Pixel `0x98d00000` |
| Modem region | `0x8d000000`, size `0x8c00000` | Redmi and Pixel `0x8b000000`, different sizes |
| ADSP region | `0x97f00000`, size `0x2800000` | Pixel `0x96b00000` |
| WLAN rails | cx-mx L9A 640 mV, xo L1C, rfa L2C, ch0 L10C | same names on Redmi. L11C must not be revoted |
| BT UART | `0x88c000` (QUP0 SE3, hsuart0) | same on Pixel |
| BT rails | io L10A, core L2C, pa L10C, xtal L1C | same on Pixel |
| Audio amp | TI TAS2562 + WCD937x | Pixel: Cirrus CS35L41 + WCD937x |
| Fuel gauge / charger | s2mu106/107 | different on both |
| Haptics | Samsung vibrator node | Redmi AW8624 |
| Panel | S6E3FA9, 1080x2400 | different on both |

Pixel's Wi-Fi notes record that a wrong MSA region size crashed the firmware at vdev
start, so check MSA first if the A71 firmware crashes the same way. Note the A71 has
two sizes in its DT (above); which one the firmware needs is not known.

## Firmware sources

Stock package `A715FXXSBDXB1` in `Desktop\misc\misc\71stuff\ROMS\Stock` matches the
phone's bootloader. Keep firmware out of git.

- `BL_*.tar.md5`: `NON-HLOS.bin.lz4` (radio and DSP images expected here), `dspso.bin.lz4`
- `CP_*.tar.md5`: `modem.bin.lz4`
- `AP_*.tar.md5`: `super.img.lz4` (vendor: GPU firmware expected in `/firmware`)

Contents of those images have not been opened yet. The Samsung-signed `a615_zap` must
come from here: the donors' files are signed by other vendors.

## Expected difficulty on the A71

| Feature | Reuse | New work | Main risk |
|---|---|---|---|
| Wi-Fi | wland, kernel en0 | addresses, rails, BDF | Samsung TZ/modem differences |
| Bluetooth | kernel UART, shim | rails check, firmware | low if Wi-Fi's modem path works |
| GPU/Metal | kernel driver, Metal bundle | zap, OPP for 730 | Samsung TZ on zap load |
| Audio | ADSP boot, APR, HAL plug-in | TAS2562 driver, routing | no speaker protection: keep volume low |
| Sensors | ADSP path | registry files | parts differ |
| Battery, haptics, backlight | interface only | new drivers | low |

Cellular is out of scope in the donors too.

## Prerequisites and order

1. USB console on the A71 (0089), then USB networking. The donors install userspace
   over SSH, so without it each userspace test needs a root edit. Windows likely has no
   inbox CDC-ECM driver: use NCM or another path, and avoid requiring WSL.
2. Choose the base. Recommendation: Yofuness/ios-flame patches, with A71 patches on top.
   Patch numbers collide with ours from 0081; its tools assume a Linux host, so check
   which userspace builds run in CI.
3. Wi-Fi first: it reuses the modem path and gives network access.
4. Bluetooth, then GPU, then audio and sensors, which share the ADSP boot.
