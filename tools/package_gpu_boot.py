import zipfile, os, subprocess, shutil, stat, glob, sys

def remove_readonly(func, path, excinfo):
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        pass

user_home = os.path.expanduser('~')
downloads = os.path.join(user_home, 'Downloads')

if len(sys.argv) > 1:
    zip_path = sys.argv[1]
else:
    candidates = glob.glob(os.path.join(downloads, 'xnu-kernel*.zip'))
    if candidates:
        candidates.sort(key=os.path.getmtime)
        zip_path = candidates[-1]
    else:
        zip_path = os.path.join(downloads, 'xnu-kernel.zip')

print(f"Extracting kernel from {zip_path}...")
os.makedirs('build', exist_ok=True)
with zipfile.ZipFile(zip_path) as z:
    z.extract('kernel.development.sweet', path='build')

kernel_path = 'build/kernel.development.sweet'
kernel_size = os.path.getsize(kernel_path)
print(f"Extracted {kernel_path} ({kernel_size} bytes)")

with open(kernel_path, 'rb') as f:
    kdata = f.read()

assert b'Samsung S6SY771' in kdata, "Touch driver signature missing!"
assert b'SWEET GPU: GPUCC audit v2' in kdata, "GPU driver signature missing!"
print("Verified Touch (Samsung S6SY771) and GPU driver signatures in kernel!")

# Same arguments as the A71 boot image known to reach the lock screen, except
# sweetusb: 3 powers the controller up and offers the console as a serial port.
sweetusb = os.environ.get('SWEETUSB', '3')
cmdline = f"debug=0xc serial=3 cpus=8 keepsyms=1 -enable_kprintf_spam -noprogress rd=ufsroot0 unentitled_ios_sim_launch=1 ignition_level=1 wdt_test=no_user_checkin ufsrw=1 rootrw=1 launchd_unsecure_cache=1 wdt=-1 sweetfb_hz=60 sweettouchdelay=3 sweettouchbitbang=1 sweetusb={sweetusb} sweetfb_noconsole=1"
# Fork drivers with Redmi values stay off on the A71 until ported: real battery (A71 has
# s2mu106/107), DSI backlight (A71 is a command-mode panel), display register dump, page flip.
# sweetgpu is the GPU bring-up stage: 1 read-only ... 9 commands. Override with SWEETGPU.
cmdline += " sweetbattery=0 sweetbacklight=0 sweetfbdpudump=0 sweetfbflip=0 sweetfbbeam=0"
cmdline += f" sweetgpu={int(os.environ.get('SWEETGPU', '1'))}"
# Less console work: copies of /dev/null writes and the IOKit trace. Empty SWEETQUIET keeps them.
if os.environ.get('SWEETQUIET', '1'):
    cmdline += " sweetnull=none sweetiolog=none"
# Extra test arguments, such as sweetexeclog=1 or ufslogsecs=1.
if os.environ.get('SWEETARGS'):
    cmdline += " " + os.environ['SWEETARGS']

print("\n1. Generating ADT...")
subprocess.run(['python', 'tools/mkadt.py', 'build/a71_highmem.adt', '--highmem'], check=True)
adt_path = 'build/a71_highmem.adt'
# The phone's own GPU firmware goes into the boot image only: never into git.
fw = 'research/a71-firmware'
if os.path.exists(os.path.join(fw, 'a615_zap.mdt')):
    import hashlib
    def fw_args(option, name):
        path = os.path.join(fw, name)
        digest = hashlib.sha256(open(path, 'rb').read()).hexdigest()
        return [option, path, option + '-sha256', digest]
    gmu = os.path.join(fw, 'a618_gmu.bin')
    adt_path = 'build/a71_highmem_fw.adt'
    if os.path.exists(adt_path):
        os.remove(adt_path)
    subprocess.run(['python', 'tools/a71-adreno-firmware.py', 'build/a71_highmem.adt', gmu, adt_path,
                    '--sha256', hashlib.sha256(open(gmu, 'rb').read()).hexdigest()]
                   + fw_args('--sqe-firmware', 'a630_sqe.fw') + fw_args('--zap-metadata', 'a615_zap.mdt')
                   + fw_args('--zap-code', 'a615_zap.b02'), check=True)

print("\n2. Packaging SWEETXNU payload...")
subprocess.run(['python', 'tools/mkpayload.py', kernel_path, adt_path, 'build/a71_payload.bin', '--cmdline', cmdline], check=True)

out_img = os.path.join('shim', 'out', 'shim-boot.img')
out_tar = os.path.join('shim', 'out', 'shim-boot.tar')

print(f"\n3. Building boot.img: {out_img}...")
subprocess.run(['python', 'tools/mkbootv2.py', '--kernel', 'shim/out/Image', '--ramdisk', 'build/a71_payload.bin', '--dtb', 'dtb/boot_unpacked/dtb', '-o', out_img], check=True)

print(f"\n4. Packaging Odin TAR: {out_tar}...")
subprocess.run(['python', 'tools/samsung-tar.py', out_img, out_tar], check=True)

if os.path.exists('build/unpacked_test'):
    shutil.rmtree('build/unpacked_test', onexc=remove_readonly)

print("\nSUCCESS! Ready to flash via TWRP or Odin!")
