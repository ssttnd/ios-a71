#!/usr/bin/env python3
"""Build the Sunfish-derived SweetMetal2.bundle for iOS 16 arm64 on macOS.

The bundle is metal2vulkan (steelbrain, pinned by UniXNU) + the NativeMetal
bridge + Turnip with the darwin kernel backend. It talks to sweet's existing
NativeAdreno ABI1 user client; the kernel publishes the plugin name
SweetMetal2 (xnu/patches/0122). Sources are fetched at pinned revisions; this
script only builds, it never installs or runs anything on a device.

    python3 tools/build-sweet-metal2.py

Output: userspace/out/SweetMetal2.bundle and userspace/out/metal2-build.json.
"""
from pathlib import Path
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'research/sweet-metal2-build'
SUNFISH_PIN = 'f09fdb58a2c074d131072947b247240532b7d97d'
UNIXNU_PIN = 'ccd8b95f486aa12925cde2d24f0c1f69b57aa9da'
MESA_COMMIT = '85fda3b36405bcaea8bffc64aa4f2651dc90770d'
M2V_COMMIT = '720ef1ce9ae2a2068999a8675717418b9416283e'
DEPLOYMENT = 'arm64-apple-ios16.0'


def run(*args, **kwargs):
    print('+', ' '.join(str(a) for a in args), flush=True)
    return subprocess.run([str(a) for a in args], check=True, **kwargs)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def checkout(url, revision, path):
    if path.exists():
        raise SystemExit('Refusing to reuse an existing build checkout: ' + str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    run('git', 'init', '-q', path)
    run('git', '-C', path, 'fetch', '-q', '--depth=1', url, revision)
    run('git', '-C', path, 'checkout', '-q', '--detach', 'FETCH_HEAD')
    actual = subprocess.check_output(['git', '-C', str(path), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != revision:
        raise SystemExit('Source revision mismatch: %s != %s' % (actual, revision))


def patch_series(repo, patches):
    """Apply patches in order and return their hashes for the build report."""
    applied = {}
    for patch in patches:
        if not patch.is_file():
            raise SystemExit('Missing patch: ' + str(patch))
        run('git', '-C', repo, 'apply', patch)
        applied[str(patch.relative_to(ROOT))] = sha256(patch)
        print('applied', patch.name, flush=True)
    return applied


def patch_sections(text):
    lines = text.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines)
              if line.startswith('--- a/') or line.startswith('diff --git ')]
    return [''.join(lines[i:starts[n + 1] if n + 1 < len(starts) else len(lines)])
            for n, i in enumerate(starts)]


def patch_file_section(patch, rel):
    return ''.join(section for section in patch_sections(patch.read_text())
                   if ('a/' + rel) in section.split('\n')[0]
                   or ('b/' + rel) in section.split('\n')[0])


def kernel_abi_header():
    """Reconstruct the userspace ABI header from the kernel patch series.

    The kernel is authoritative. 0110 creates iokit/Kernel/arm/unixnu/
    NativeAdrenoABI.h; later patches add SubmitResources and the 4096-BO flag.
    Turnip compiles against exactly this header through -Dtu-darwin-abi-dir.
    """
    rel = 'iokit/Kernel/arm/unixnu/NativeAdrenoABI.h'
    patches = sorted((ROOT / 'xnu/patches').glob('01*.patch'))
    if not patches:
        raise SystemExit('No kernel patches; cannot derive the ABI header')
    created = next((p for p in patches
                    if p.name.startswith('0110-') and 'new file mode' in patch_file_section(p, rel)), None)
    if created is None:
        raise SystemExit('No patch creates ' + rel)
    section = patch_file_section(created, rel)
    lines = [line[1:] for line in section.splitlines()
             if line.startswith('+') and not line.startswith('+++')]
    # Apply the follow-up fragments outside the project repository: git apply
    # treats paths relative to the worktree root and skips a repeated file.
    with tempfile.TemporaryDirectory(prefix='sweet-metal2-abi-') as tmp:
        staging = Path(tmp)
        (staging / rel).parent.mkdir(parents=True)
        (staging / rel).write_text('\n'.join(lines) + '\n')
        for patch in patches:
            if patch == created:
                continue
            fragment = patch_file_section(patch, rel)
            if fragment:
                run('git', 'apply', '-p1', '-', cwd=staging, input=fragment, text=True)
        header = WORK / 'abi' / rel
        header.parent.mkdir(parents=True)
        shutil.copy2(staging / rel, header)
    text = header.read_text()
    for required in ('NativeAdrenoMethodSubmitResources = 17',
                     'NativeAdrenoParamsCompositor4096 = 4',
                     'NativeAdrenoAbiMaxResources = 1024',
                     'sizeof(NativeAdrenoAbiSubmitResources) == 16808'):
        if required not in text:
            raise SystemExit('Kernel ABI header lost ' + required)
    return header


def python_env(unixnu):
    """Mesa needs Mako and a current meson; keep them out of the system."""
    venv = WORK / 'venv'
    if not (venv / 'bin/python3').exists():
        run(sys.executable, '-m', 'venv', venv)
        run(venv / 'bin/python3', '-m', 'pip', 'install', '--quiet',
            '-r', unixnu / 'scripts/requirements-adreno-compiler.txt')
    env = dict(os.environ)
    env['PATH'] = str(venv / 'bin') + os.pathsep + env.get('PATH', '')
    return env


def sdk_tool(env, *args):
    return subprocess.check_output(['xcrun', '--sdk', 'iphoneos', *args], text=True, env=env).strip()


def build_turnip(unixnu, sunfish, mesa, abi, env):
    compiler = json.loads((unixnu / 'scripts/data/adreno-compiler-inputs.json').read_text())
    applied = patch_series(mesa, [unixnu / name for name in compiler['patches']])
    applied.update(patch_series(mesa, sorted((sunfish / 'phone/turnip/patches').glob('*.patch'))))

    if not shutil.which('glslangValidator', path=env.get('PATH')):
        raise SystemExit('glslangValidator is required (brew install glslang; add it to PATH)')
    sdk = sdk_tool(env, '--show-sdk-path')
    clang = sdk_tool(env, '--find', 'clang')
    clangxx = sdk_tool(env, '--find', 'clang++')
    ar = sdk_tool(env, '--find', 'ar')
    strip = sdk_tool(env, '--find', 'strip')
    build = WORK / 'turnip' / 'build-ios'
    build.parent.mkdir(parents=True, exist_ok=True)
    cross = WORK / 'turnip' / 'ios16.cross'
    flags = ["'-target'", "'%s'" % DEPLOYMENT, "'-isysroot'", "'%s'" % sdk,
             "'-fstack-protector-strong'"]
    cross.write_text('''[binaries]
c = '%s'
cpp = '%s'
objc = '%s'
ar = '%s'
strip = '%s'
pkg-config = '/usr/bin/false'
[host_machine]
system = 'darwin'
cpu_family = 'aarch64'
cpu = 'arm64'
endian = 'little'
[properties]
needs_exe_wrapper = true
[built-in options]
c_args = [%s]
cpp_args = [%s]
objc_args = [%s]
c_link_args = [%s]
cpp_link_args = [%s]
objc_link_args = [%s]
''' % (clang, clangxx, clang, ar, strip, ','.join(flags), ','.join(flags),
       ','.join(flags), ','.join(flags), ','.join(flags), ','.join(flags)))

    options = [
        '-Dgallium-drivers=', '-Dvulkan-drivers=freedreno', '-Dfreedreno-kmds=darwin',
        '-Dtu-darwin-abi-dir=' + str(abi), '-Dplatforms=macos', '-Dglx=disabled',
        '-Degl=disabled', '-Dgbm=disabled', '-Dopengl=false', '-Dgles1=disabled',
        '-Dgles2=disabled', '-Dllvm=disabled', '-Dshared-glapi=disabled',
        '-Dxmlconfig=disabled', '-Dshader-cache=disabled', '-Dtools=',
        '-Dbuild-tests=false', '-Dvideo-codecs=', '-Dvulkan-layers=',
        '-Dlibunwind=disabled', '-Dlmsensors=disabled', '-Dvalgrind=disabled',
        '-Dexpat=disabled', '-Dzstd=disabled', '-Dbuildtype=release', '-Db_ndebug=true',
    ]
    configure = (WORK / 'turnip' / 'configure-ios.log').open('w')
    run(WORK / 'venv/bin/meson', 'setup', build, mesa,
        '--wrap-mode=nodownload', '--cross-file', cross, *options,
        cwd=WORK / 'turnip', env=env, stdout=configure, stderr=subprocess.STDOUT)
    configure.close()
    log = (WORK / 'turnip' / 'build-ios.log').open('w')
    run('ninja', '-C', build,
        'src/freedreno/vulkan/libvulkan_freedreno.dylib',
        cwd=WORK / 'turnip', env=env, stdout=log, stderr=subprocess.STDOUT)
    log.close()
    lib = build / 'src/freedreno/vulkan/libvulkan_freedreno.dylib'
    exports = subprocess.check_output(['nm', '-gU', str(lib)], text=True)
    if ' T _vkGetInstanceProcAddr' not in exports:
        raise SystemExit('Turnip has no vkGetInstanceProcAddr export')
    return lib, applied


def build_bridge(metal2vulkan, env):
    sunfish = WORK / 'donor/sunfish'
    patches = sorted((sunfish / 'phone/metal2/bridge/patches').glob('*.patch'))
    # Sweet's own metal2vulkan fixes apply after the donor series.
    patches += sorted((ROOT / 'userspace/metal2/bridge-patches').glob('*.patch'))
    applied = patch_series(metal2vulkan, patches)

    llvm_as = shutil.which('llvm-as', path=env.get('PATH'))
    if not llvm_as:
        for prefix in ('/opt/homebrew/opt/llvm/bin', '/usr/local/opt/llvm/bin'):
            candidate = Path(prefix) / 'llvm-as'
            if candidate.is_file():
                llvm_as = str(candidate)
                break
    if not llvm_as:
        raise SystemExit('llvm-as is required (brew install llvm; add it to PATH)')

    bridge = WORK / 'bridge'
    bridge.mkdir(parents=True)
    fixtures = metal2vulkan / 'crates/metal2vulkan-validation/fixtures/public'
    run(llvm_as, '-o', bridge / 'runtime_examples.air',
        fixtures / 'examples/runtime_examples.ll', env=env)
    run(llvm_as, '-o', bridge / 'function_constant.air',
        fixtures / 'kernel_function_constant_gate_fold.ll', env=env)
    (bridge / 'Cargo.toml').write_text('''[package]
name = "native-metal-bridge"
version = "0.1.0"
edition = "2024"
rust-version = "1.98"
[workspace]
[lib]
crate-type = ["staticlib"]
path = "%s"
[dependencies]
metal2vulkan = { path = "%s", features = ["ash"] }
metal2vulkan-runtime = { path = "%s" }
libc = "0.2"
sha2 = { version = "0.10", features = ["asm"] }
[profile.release]
panic = "unwind"
debug = 1
''' % (sunfish / 'phone/metal2/bridge/NativeMetalBridge.rs',
       metal2vulkan / 'crates/metal2vulkan',
       metal2vulkan / 'crates/metal2vulkan-runtime'))

    triple = 'aarch64-apple-ios'
    cargo_env = dict(env)
    cargo_env.update(NMB_FIXTURE_AIR=str(bridge / 'runtime_examples.air'),
                     NMB_FC_AIR=str(bridge / 'function_constant.air'),
                     IPHONEOS_DEPLOYMENT_TARGET='16.0',
                     MACOSX_DEPLOYMENT_TARGET='14.0')
    log = (bridge / 'build-ios.log').open('w')
    run('cargo', '+1.98.0', 'build', '--release', '--manifest-path', bridge / 'Cargo.toml',
        '--target', triple, '--target-dir', bridge / 'target',
        env=cargo_env, stdout=log, stderr=subprocess.STDOUT)
    log.close()
    lib = bridge / 'target' / triple / 'release/libnative_metal_bridge.a'
    if not lib.is_file():
        raise SystemExit('Missing bridge static library')
    return lib, applied


def assemble(turnip, bridge_lib, env):
    sunfish = WORK / 'donor/sunfish'
    out = ROOT / 'userspace/out'
    bundle = out / 'SweetMetal2.bundle'
    if bundle.exists():
        raise SystemExit('Refusing to overwrite a prior bundle: ' + str(bundle))
    sdk = sdk_tool(env, '--show-sdk-path')
    # Build and symbolicate outside the bundle: macOS dsymutil also drops a copy
    # next to its input, and a 186 MB dSYM must never be sealed into the bundle
    # that gets installed on the phone.
    exe = out / 'SweetMetal2.bin'
    run('xcrun', 'clang++', '-target', DEPLOYMENT, '-isysroot', sdk, '-O2', '-g',
        '-std=c++17', '-fobjc-arc', '-bundle',
        '-Wno-deprecated-declarations', '-Wno-unused-function',
        '-I' + str(sunfish / 'phone/metal2'), '-I' + str(sunfish / 'phone/metal2/bridge'),
        sunfish / 'phone/metal2/SweetMetal2.mm',
        sunfish / 'phone/metal2/NativeBridgeMetalFrontend.mm',
        bridge_lib, '-Wl,-dead_strip', '-lc++',
        '-framework', 'Foundation', '-framework', 'Metal', '-framework', 'IOSurface',
        '-framework', 'IOKit', '-framework', 'CoreFoundation', '-o', exe, env=env)
    try:
        run('dsymutil', exe, '-o', out / 'SweetMetal2.dSYM', env=env)
    except subprocess.CalledProcessError:
        print('dsymutil failed; continuing without symbols', flush=True)
    run('strip', '-x', exe, env=env)

    bundle.mkdir(parents=True)
    shutil.copy2(exe, bundle / 'SweetMetal2')
    shutil.copy2(turnip, bundle / 'libvulkan_freedreno.dylib')
    run('strip', '-x', bundle / 'libvulkan_freedreno.dylib', env=env)
    (bundle / 'Info.plist').write_bytes(plistlib.dumps(dict(
        CFBundleExecutable='SweetMetal2',
        CFBundleIdentifier='org.sweet.SweetMetal2',
        CFBundleName='SweetMetal2',
        CFBundlePackageType='BNDL',
        CFBundleInfoDictionaryVersion='6.0',
        CFBundleShortVersionString='0.1',
        CFBundleVersion='1',
        MinimumOSVersion='16.0',
        NSPrincipalClass='SweetMTL2Device')))

    licenses = bundle / 'Licenses'
    licenses.mkdir()
    for source, name in ((sunfish / 'LICENSE', 'Sunfish-MIT.txt'),
                         (sunfish / 'phone/metal2/bridge/LICENSE', 'NativeMetalBridge-MIT.txt')):
        shutil.copy2(source, licenses / name)
    for source in (WORK / 'metal2vulkan').glob('LICENSE*'):
        if source.is_file():
            shutil.copy2(source, licenses / ('metal2vulkan-' + source.name))
    for source in (WORK / 'donor/unixnu/LICENSE', WORK / 'mesa/docs/license.rst'):
        if source.is_file():
            shutil.copy2(source, licenses / (('Mesa-' if source.name == 'license.rst' else 'UniXNU-') + source.name))
    for source in (WORK / 'mesa/COPYING', WORK / 'mesa/LICENSE'):
        if source.is_file():
            shutil.copy2(source, licenses / ('Mesa-' + source.name))

    run('codesign', '--force', '--sign', '-', bundle / 'libvulkan_freedreno.dylib', env=env)
    run('codesign', '--force', '--sign', '-', bundle, env=env)
    run('codesign', '--verify', '--strict', bundle, env=env)
    assert not list(bundle.rglob('*.dSYM')), 'dSYM sealed inside the bundle'
    return bundle


def main():
    if sys.platform != 'darwin':
        raise SystemExit('This build requires macOS and the iPhoneOS SDK')
    if WORK.exists():
        raise SystemExit('Refusing to reuse an existing build tree: ' + str(WORK))
    env = dict(os.environ)
    donor = WORK / 'donor'
    checkout('https://github.com/Yofuness/ios-flame.git', SUNFISH_PIN, donor / 'sunfish')
    checkout('https://github.com/UniXNU/UniXNU.git', UNIXNU_PIN, donor / 'unixnu')
    checkout('https://chromium.googlesource.com/external/gitlab.freedesktop.org/mesa/mesa',
             MESA_COMMIT, WORK / 'mesa')
    checkout('https://github.com/steelbrain/metal2vulkan', M2V_COMMIT, WORK / 'metal2vulkan')

    frontend_patches = patch_series(
        donor / 'sunfish', sorted((ROOT / 'userspace/metal2/patches').glob('*.patch')))

    env = python_env(donor / 'unixnu')
    abi = kernel_abi_header()
    out = ROOT / 'userspace/out'
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(abi, out / 'NativeAdrenoABI.h')
    turnip, mesa_patches = build_turnip(donor / 'unixnu', donor / 'sunfish', WORK / 'mesa', abi.parent, env)
    bridge_lib, bridge_patches = build_bridge(WORK / 'metal2vulkan', env)
    bundle = assemble(turnip, bridge_lib, env)

    report = {
        'sources': {'sunfish': SUNFISH_PIN, 'unixnu': UNIXNU_PIN,
                    'mesa': MESA_COMMIT, 'metal2vulkan': M2V_COMMIT},
        'deployment_target': '16.0',
        'kernel_plugin_name': 'SweetMetal2',
        'kernel_abi_header': 'userspace/out/NativeAdrenoABI.h',
        'kernel_abi_sha256': sha256(abi),
        'root_prerequisites': {
            'shader_cache': '/private/var/local/m2v-cache/backboardd',
            'owner_uid': 501, 'owner_gid': 501, 'mode': '0700',
            'provision_tool': 'tools/prepare-sweet-metal2-cache.sh',
        },
        'mesa_patches': mesa_patches,
        'bridge_patches': bridge_patches,
        'frontend_patches': frontend_patches,
        'runtime_qualified': False,
        'installed': False,
        'hardware_tested': False,
        'files': {str(p.relative_to(bundle)): sha256(p)
                  for p in bundle.rglob('*') if p.is_file()},
    }
    (out / 'metal2-build.json').write_text(json.dumps(report, indent=2) + '\n')
    print('SweetMetal2 bundle built; not installed or runtime-qualified.', flush=True)


if __name__ == '__main__':
    main()
