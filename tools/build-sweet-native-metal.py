#!/usr/bin/env python3
"""Build the direct AIR/freedreno SweetNativeMetal.bundle (macOS, iOS SDK).

This driver has no metal2vulkan or Turnip dependency. Sources are pinned and
exported into a fresh private directory, then adapted by prepare-sweet-native-
metal.py. --prepare-only works on Linux for inspecting/reviewing generated code.
A local --donor-repo is read with git archive at the pinned revision, never edited.
"""
from pathlib import Path
import argparse
import hashlib
import importlib.util
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tarfile
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
NATIVE=ROOT/'userspace/native-metal'
INPUTS=json.loads((NATIVE/'inputs.json').read_text())
WORK=ROOT/'research/sweet-native-metal-build'

def run(*args,**kw):
    print('+',' '.join(str(v) for v in args),flush=True)
    return subprocess.run([str(v) for v in args],check=True,**kw)

def output(*args,**kw):
    return subprocess.check_output([str(v) for v in args],text=True,**kw).strip()

def logged(path,*args,**kwargs):
    with path.open('w') as log:
        try:run(*args,stdout=log,stderr=subprocess.STDOUT,**kwargs)
        except subprocess.CalledProcessError:
            log.flush()
            print('\n'.join(path.read_text(errors='replace').splitlines()[-100:]),flush=True)
            raise

def sha(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
    return digest.hexdigest()

def export_donor(repo):
    if repo is None:
        repo=WORK/'donor-git'
        run('git','init','-q',repo)
        run('git','-C',repo,'fetch','--depth=1',INPUTS['donor_repository'],INPUTS['donor_commit'])
    revision=output('git','-C',repo,'rev-parse',INPUTS['donor_commit']+'^{commit}')
    if revision!=INPUTS['donor_commit']:raise SystemExit('Donor pin mismatch')
    archive=WORK/'donor.tar'
    with archive.open('wb') as stream:
        run('git','-C',repo,'archive',revision,'LICENSE','tools/mesa',
            'userland/src/redwoodmetal.m','userland/src/redwood_drawable.h',
            'userland/src/redwooddrawable.m',stdout=stream)
    donor=WORK/'donor';donor.mkdir()
    with tarfile.open(archive) as tar:tar.extractall(donor,filter='data')
    run(sys.executable,ROOT/'tools/prepare-sweet-native-metal.py',donor)
    return donor

def exact(text,old,new,count=1):
    if text.count(old)!=count:raise SystemExit(f'Mesa context changed ({text.count(old)} matches): {old[:90]!r}')
    return text.replace(old,new)

def adapt_submit_merging(mesa):
    """Merge a Metal command's Gallium batch submits into one NativeAdreno ioctl.

    1a6b5dd active traces: ~7 ioctls per command buffer, 2.0s of 5s inside the
    submit ioctl. 0172 SubmitResources publishes every listed resource on every
    call, walking each imported IOSurface's pages (kernelMaintainsImport only
    covers the legacy publishMapped path), so per-ioctl cost is fixed and large.
    Upstream refuses to defer submits naming shared BOs (implicit-sync rule) or
    more than 30 BOs, so CoreAnimation batches were never merged.
    NativeAdreno has no kernel implicit sync. CPU access still flushes deferred
    work first (fd_bo_cpu_prep -> bo_flush), and fence waits flush through
    fd_pipe_wait; no_implicit_sync stays false so shared-BO CPU_PREP still
    reaches the transport's cache ownership path. Deferred submits keep their
    BO references until merged. A merged list stays within one ABI request
    (16 IBs) and the 1024-entry resource table, so the transport never resends
    a resource list for a second chunk.
    """
    p=mesa/'src/freedreno/drm/freedreno_ringbuffer_sp.c';s=p.read_text()
    s=exact(s,'''static bool
should_defer(struct fd_submit *submit)
{
   struct fd_submit_sp *fd_submit = to_fd_submit_sp(submit);

   /* if too many bo's, it may not be worth the CPU cost of submit merging: */
   if (fd_submit->nr_bos > 30)
      return false;

   /* On the kernel side, with 32K ringbuffer, we have an upper limit of 2k
    * cmds before we exceed the size of the ringbuffer, which results in
    * deadlock writing into the RB (ie. kernel doesn't finish writing into
    * the RB so it doesn't kick the GPU to start consuming from the RB)
    */
   if (submit->pipe->dev->deferred_cmds > 128)
      return false;

   return true;
}
''','''/* Sweet NativeAdreno: one SubmitResources request carries 16 IBs and 1024
 * resources. Every request revalidates and cache-publishes its whole list.
 */
#define SWEET_MERGE_MAX_CMDS 16
#define SWEET_MERGE_MAX_BOS 1024

/* Upper bound of the merged resource table if this not-yet-prepared submit
 * joined the deferred list: flush_prep appends one ring BO per cmd.
 */
static bool
sweet_merge_overflows(struct fd_device *dev, struct fd_submit *submit)
{
   if (list_is_empty(&dev->deferred_submits))
      return false;
   unsigned cmds = fd_ringbuffer_cmd_count(submit->primary);
   unsigned bos = to_fd_submit_sp(submit)->nr_bos + cmds;
   foreach_submit (deferred, &dev->deferred_submits)
      bos += to_fd_submit_sp(deferred)->nr_bos;
   return dev->deferred_cmds + cmds > SWEET_MERGE_MAX_CMDS ||
          bos > SWEET_MERGE_MAX_BOS;
}

static bool
should_defer(struct fd_submit *submit)
{
   struct fd_submit_sp *fd_submit = to_fd_submit_sp(submit);
   struct fd_device *dev = submit->pipe->dev;

   /* Earlier deferred work was flushed by sweet_merge_overflows() when this
    * submit would not fit beside it; refuse only a submit too large alone.
    */
   if (dev->deferred_cmds + fd_ringbuffer_cmd_count(submit->primary) > SWEET_MERGE_MAX_CMDS)
      return false;
   if (fd_submit->nr_bos > SWEET_MERGE_MAX_BOS)
      return false;

   return true;
}
''')
    s=exact(s,'''   if (!list_is_empty(&dev->deferred_submits) &&
       (last_submit(&dev->deferred_submits)->pipe != submit->pipe)) {
      flush_deferred_submits(dev);
   }
''','''   if (!list_is_empty(&dev->deferred_submits) &&
       ((last_submit(&dev->deferred_submits)->pipe != submit->pipe) ||
        sweet_merge_overflows(dev, submit))) {
      flush_deferred_submits(dev);
   }
''')
    s=exact(s,'''   if (!use_fence_fd && !has_shared && should_defer(submit)) {''',
'''   /* Sweet: shared IOSurface BOs are not a deferral barrier; see
    * build-sweet-native-metal.py adapt_submit_merging for the ordering rules.
    */
   (void)has_shared;
   if (!use_fence_fd && should_defer(submit)) {''')
    p.write_text(s)

def prepare_mesa(donor,archive):
    if archive is None:
        archive=WORK/('mesa-'+INPUTS['mesa_version']+'.tar.xz')
        print('Downloading pinned Mesa archive',flush=True)
        with urllib.request.urlopen(INPUTS['mesa_url'],timeout=120) as source,archive.open('wb') as destination:
            shutil.copyfileobj(source,destination)
    if sha(archive)!=INPUTS['mesa_sha256']:raise SystemExit('Mesa archive checksum mismatch')
    with tarfile.open(archive) as tar:tar.extractall(WORK,filter='data')
    mesa=WORK/('mesa-'+INPUTS['mesa_version'])
    # Runtime library only: the donor air-ir3.patch modifies standalone tools,
    # which are deliberately not built here. Our Meson target below is explicit.
    for name in ('air-frontend.patch','freedreno-darwin.patch','ir3-round-default.patch',
                 'ir3-shift-split.patch','ir3-ssbo-subdword.patch'):
        with (donor/'tools/mesa'/name).open('rb') as patch:
            run('patch','--batch','--forward','--fuzz=0','-p1',cwd=mesa,stdin=patch)
    adapt_submit_merging(mesa)
    shutil.copytree(donor/'tools/mesa/air',mesa/'src/compiler/air')
    shutil.copytree(NATIVE/'transport',mesa/'src/redwood-drm')
    # Header reconstructed from the same tracked kernel patch series as Metal2.
    spec=importlib.util.spec_from_file_location('sweet_abi',ROOT/'tools/build-sweet-metal2.py')
    abi=importlib.util.module_from_spec(spec);spec.loader.exec_module(abi);abi.WORK=WORK
    header=abi.kernel_abi_header()
    shutil.copy2(header,mesa/'src/redwood-drm/NativeAdrenoABI.h')
    drm=mesa/'src/freedreno/drm/meson.build'
    text=drm.read_text().replace('../../redwood-drm/redwood_drm.c','../../redwood-drm/sweet_drm.cpp')
    if 'sweet_drm.cpp' not in text:raise SystemExit('MSM transport Meson integration missing')
    drm.write_text(text)
    # The custom winsys needs IOSurface lookup/retention and C++17. Every external
    # selector remains ABI1, not Linux ioctl numbers sent to an IOKit connection.
    p=mesa/'meson.build';text=p.read_text()
    needle="link_args : ['-framework', 'IOKit', '-framework', 'CoreFoundation'],"
    if text.count(needle)!=1:raise SystemExit('Missing Darwin framework dependency')
    p.write_text(text.replace(needle,"link_args : ['-framework', 'IOKit', '-framework', 'CoreFoundation', '-framework', 'IOSurface'],"))
    p=mesa/'src/gallium/meson.build'
    p.write_text(p.read_text()+'''
# Sweet native Metal C ABI. AIR -> NIR -> Gallium/ir3, with no Vulkan target.
libsweetnative = shared_library(
  'SweetNativeBackend', files('../compiler/air/air_metal.c'),
  include_directories : [inc_include, inc_src, inc_mapi, inc_mesa, inc_gallium,
    inc_gallium_aux, inc_gallium_winsys, inc_gallium_winsys_sw,
    include_directories('drivers')],
  dependencies : [dep_m, idep_air, idep_mesautil, driver_freedreno,
    idep_xmlconfig, dep_libdrm],
  link_with : [libgallium, libgalliumvl_stub, libfreedrenowinsys],
  gnu_symbol_visibility : 'default',
  link_args : ['-Wl,-install_name,@loader_path/libSweetNativeBackend.dylib', '-Wl,-headerpad_max_install_names'],
  build_by_default : false, install : false,
)
''')
    return mesa,header

def main():
    global WORK
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--donor-repo',type=Path)
    parser.add_argument('--mesa-archive',type=Path)
    parser.add_argument('--prepare-only',action='store_true',help='export/adapt donor without compiling')
    parser.add_argument('--work',type=Path,default=WORK)
    args=parser.parse_args()
    WORK=args.work.resolve()
    if WORK.exists():raise SystemExit('Refusing to reuse build tree: '+str(WORK))
    if not args.prepare_only and sys.platform!='darwin':raise SystemExit('Full bundle build requires macOS and the iPhoneOS SDK')
    WORK.mkdir(parents=True)
    donor=export_donor(args.donor_repo)
    if args.prepare_only:
        print('Prepared source only; no binary compiled.',flush=True);return
    mesa,header=prepare_mesa(donor,args.mesa_archive)
    sdk=output('xcrun','--sdk','iphoneos','--show-sdk-path')
    clang=output('xcrun','--sdk','iphoneos','--find','clang')
    clangxx=output('xcrun','--sdk','iphoneos','--find','clang++')
    flags=['-target','arm64-apple-ios16.0','-isysroot',sdk,'-O2','-fstack-protector-strong']
    venv=WORK/'venv';run(sys.executable,'-m','venv',venv)
    run(venv/'bin/python3','-m','pip','install','meson==1.7.2','Mako==1.3.10','PyYAML==6.0.2','packaging==24.2')
    env=dict(os.environ);env['PATH']=str(venv/'bin')+os.pathsep+env.get('PATH','')
    cross=WORK/'ios.cross'
    cross.write_text('''[binaries]
c = %s
cpp = %s
objc = %s
ar = %s
strip = %s
pkg-config = '/usr/bin/false'
[host_machine]
system = 'darwin'
subsystem = 'ios'
cpu_family = 'aarch64'
cpu = 'arm64'
endian = 'little'
[properties]
needs_exe_wrapper = true
[built-in options]
c_args = %s
cpp_args = %s
c_link_args = %s
cpp_link_args = %s
cpp_std = 'c++17'
'''%(repr(clang),repr(clangxx),repr(clang),repr(output('xcrun','--find','ar')),
     repr(output('xcrun','--find','strip')),repr(flags),repr(flags),repr(flags),repr(flags)))
    build=WORK/'build-ios'
    options=['-Dgallium-drivers=freedreno','-Dfreedreno-kmds=msm','-Dvulkan-drivers=',
      '-Dplatforms=','-Dglx=disabled','-Degl=disabled','-Dgbm=disabled','-Dosmesa=false',
      '-Dopengl=false','-Dgles1=disabled','-Dgles2=disabled','-Dllvm=disabled',
      '-Dshared-glapi=disabled','-Dzstd=disabled','-Dxmlconfig=disabled','-Dexpat=disabled',
      '-Dshader-cache=disabled','-Dvalgrind=disabled','-Dlibunwind=disabled','-Dglvnd=disabled',
      '-Dvideo-codecs=','-Dtools=','-Dgallium-vdpau=disabled','-Dgallium-va=disabled',
      '-Dgallium-xa=disabled','-Dlmsensors=disabled','-Dbuild-tests=false',
      '-Dbuildtype=release','-Db_ndebug=true']
    logged(WORK/'configure.log',venv/'bin/meson','setup',build,mesa,'--cross-file',cross,'--wrap-mode=nodownload',*options,env=env)
    # Capture source diagnostics early, before the full Mesa dependency graph.
    src=donor/'userland/src';air=mesa/'src/compiler/air'
    frontend=WORK/'SweetNativeMetal.o';drawable=WORK/'SweetNativeDrawable.o'
    common=[*flags,'-fno-objc-arc','-fblocks','-Wall','-Werror=implicit-function-declaration',
            '-I'+str(air),'-I'+str(src)]
    run(clang,*common,'-c',src/'redwoodmetal.m','-o',frontend)
    run(clang,*common,'-c',src/'redwooddrawable.m','-o',drawable)
    logged(WORK/'ninja.log','ninja','-j','3','-C',build,'src/gallium/libSweetNativeBackend.dylib',env=env)
    out=ROOT/'userspace/out';out.mkdir(parents=True,exist_ok=True)
    bundle=out/'SweetNativeMetal.bundle'
    if bundle.exists():raise SystemExit('Refusing to overwrite prior bundle')
    bundle.mkdir();backend=bundle/'libSweetNativeBackend.dylib'
    shutil.copy2(build/'src/gallium/libSweetNativeBackend.dylib',backend)
    run('install_name_tool','-id','@loader_path/libSweetNativeBackend.dylib',backend)
    executable=bundle/'SweetNativeMetal'
    run(clangxx,*flags,'-bundle','-Wl,-dead_strip',frontend,drawable,backend,
        '-framework','Foundation','-framework','Metal','-framework','IOKit',
        '-framework','IOSurface','-framework','CoreFoundation','-lobjc','-o',executable)
    (bundle/'Info.plist').write_bytes(plistlib.dumps(dict(CFBundleIdentifier='org.sweet.SweetNativeMetal',
      CFBundleExecutable='SweetNativeMetal',CFBundlePackageType='BNDL',CFBundleVersion='1',
      MinimumOSVersion='16.0',NSPrincipalClass='SweetNativeMetalDevice')))
    licenses=bundle/'Licenses';licenses.mkdir()
    shutil.copy2(donor/'LICENSE',licenses/'HakiOS.txt')
    shutil.copy2(mesa/'docs/license.rst',licenses/'Mesa.txt')
    symbols=out/'native-metal-symbols';symbols.mkdir()
    for name,binary in (('backend',backend),('frontend',executable)):
        (symbols/(name+'.symbols.txt')).write_text(output('nm','-an',binary)+'\n')
        (symbols/(name+'.macho.txt')).write_text(output('otool','-l',binary)+'\n')
    for binary in (backend,executable):
        run('strip','-x',binary);run('codesign','--force','--sign','-','--pagesize','4096',binary)
    run('codesign','--force','--sign','-','--pagesize','4096',bundle)
    run('codesign','--verify','--strict',bundle)
    dependencies=output('otool','-L',executable)+'\n'+output('otool','-L',backend)
    if any(x in dependencies.lower() for x in ('vulkan','turnip','metal2vulkan')):
        raise SystemExit('Unexpected Vulkan dependency in native build')
    (out/'native-metal-dependencies.txt').write_text(dependencies+'\n')
    report=dict(INPUTS,built=True,installed=False,hardware_qualified=False,
      abi_sha256=sha(header),source_commit=output('git','-C',ROOT,'rev-parse','HEAD'),
      completion='synchronous once per command buffer; no per-draw fence waits',
      files={str(p.relative_to(bundle)):sha(p) for p in bundle.rglob('*') if p.is_file()})
    (out/'native-metal-build.json').write_text(json.dumps(report,indent=2)+'\n')
    # Older kernels select this public class/name. Keep a separate package so the
    # original SweetMetal2 installation is never overwritten by the build itself.
    legacy=out/'native-legacy'/'SweetMetal2.bundle'
    if legacy.exists():raise SystemExit('Refusing to overwrite prior compatibility bundle')
    shutil.copytree(bundle,legacy)
    shutil.rmtree(legacy/'_CodeSignature')
    (legacy/'SweetNativeMetal').unlink()
    alias=WORK/'SweetNativeLegacyAlias.m'
    alias.write_text('#import <Foundation/Foundation.h>\n'
      '@interface SweetNativeMetalDevice : NSObject @end\n'
      '@interface SweetMTL2Device : SweetNativeMetalDevice @end\n'
      '@implementation SweetMTL2Device @end\n')
    alias_obj=WORK/'SweetNativeLegacyAlias.o'
    run(clang,*common,'-c',alias,'-o',alias_obj)
    legacy_exe=legacy/'SweetMetal2'
    run(clangxx,*flags,'-bundle','-Wl,-dead_strip',frontend,drawable,alias_obj,
        legacy/'libSweetNativeBackend.dylib','-framework','Foundation',
        '-framework','Metal','-framework','IOKit','-framework','IOSurface',
        '-framework','CoreFoundation','-lobjc','-o',legacy_exe)
    (legacy/'Info.plist').write_bytes(plistlib.dumps(dict(
      CFBundleIdentifier='org.sweet.SweetMetal2',CFBundleExecutable='SweetMetal2',
      CFBundlePackageType='BNDL',CFBundleVersion='1',MinimumOSVersion='16.0',
      NSPrincipalClass='SweetMTL2Device')))
    (symbols/'legacy.symbols.txt').write_text(output('nm','-an',legacy_exe)+'\n')
    (symbols/'legacy.macho.txt').write_text(output('otool','-l',legacy_exe)+'\n')
    run('strip','-x',legacy_exe)
    run('codesign','--force','--sign','-','--pagesize','4096',legacy_exe)
    run('codesign','--force','--sign','-','--pagesize','4096',legacy)
    run('codesign','--verify','--strict',legacy)
    legacy_dependencies=output('otool','-L',legacy_exe)
    if any(x in legacy_dependencies.lower() for x in ('vulkan','turnip','metal2vulkan')):
        raise SystemExit('Unexpected Vulkan dependency in compatibility build')
    (out/'native-legacy'/'build.json').write_text(json.dumps(dict(
      source_commit=report['source_commit'],built=True,installed=False,
      hardware_qualified=False,kernel_plugin='SweetMetal2',
      kernel_class='SweetMTL2Device',required_boot_args='sweetmtl=2 sweetgles=0',
      kernel_changed=False,dependencies=legacy_dependencies,
      files={str(p.relative_to(legacy)):sha(p) for p in legacy.rglob('*') if p.is_file()}
    ),indent=2)+'\n')
    print('Built',bundle,'; hardware operation and CoreAnimation coverage remain unqualified.',flush=True)

if __name__=='__main__':main()
