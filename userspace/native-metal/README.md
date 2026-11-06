# Sweet native Metal frontend

This is an experimental direct frontend for the Redmi Note 10 Pro's A618:

```
Metal objects -> AIR reader/lowering -> NIR -> Gallium freedreno/ir3
             -> Sweet NativeAdreno ABI1 -> A618
```

The build disables Vulkan and all software rasterizers. It does not link
SweetMetal2, metal2vulkan, Turnip, or a Vulkan loader. It reuses Sweet's existing
power, GMU, SMMU and CP kernel driver; rewriting those is unnecessary for a
native Metal frontend.

## Source and ownership

`inputs.json` pins HakiOS userland at
`a79ff0772a3454fd1ef127e181e5b3bce6c9ce18` and Mesa 25.0.7 by archive SHA256.
That historical HakiOS revision contains the real AIR/Gallium runtime and Metal
objects; later split repositories do not contain the same implementation.
Its MIT license is retained in `HAKIOS-LICENSE.txt` and in built bundles. Mesa
keeps its own licenses. Apple SDKs and firmware are never committed.

`tools/prepare-sweet-native-metal.py` adapts a disposable export of that pin.
Context-checked transformations fail when source assumptions change. Both the
Objective-C frontend and C runtime use the same `air_metal.h` and
`sweet_native_ext.h`; separate handwritten copies of the ABI are removed.

- `transport/sweet_drm.cpp`: actual Sweet ABI1 transport. Mesa's internal MSM
  calls are translated locally, never sent as foreign IOKit selectors. Uses
  scoped submission 17 with complete BO residency, automatic kernel cache
  maintenance, real fences and per-BO lifetime. IOSurface IDs are local lookup
  keys; the actual CPU pages are imported via selector 16 and retained.
- `backend/`: metallib ingestion, explicit function specialization, command
  transactions and clears over the AIR/freedreno runtime.
- `frontend/`: native library/function objects and the Sweet IOSurface bridge.
  The prepared donor provides device, queue, command buffer, encoder, pipeline,
  texture, sampler, depth/stencil and argument-encoder objects.

## Changes from the donor

Discovery requires a genuine A618 backend. Buffer allocations cannot fall back
to nonexistent RedwoodGPU methods. A missing hardware screen fails creation.

Each command buffer records work, retains referenced objects, executes under a
recursive context lock, and waits for completion once at commit. Draws and
compute dispatches no longer flush/wait individually. A failed command buffer
also drains its submitted prefix. Timeout storage stays pinned; success is
never fabricated. This first implementation is synchronous at commit, not an
asynchronous Metal scheduler.

Consecutive draws to the same attachments retain the Gallium framebuffer,
allowing freedreno to batch them. The driver honors viewport/scissor rectangles,
Metal's top-left origin, `[0,1]` depth, blend constants, independent attachment
clear colors and one clear per pass. Passes containing only a clear are recorded
at `endEncoding`. IOSurface pitch, format, page offset and allocation extent are
checked rather than substituted.

Native function objects retain exact specialization bytes. The compiler receives
provided indexed/named constants; missing values are not silently set to zero.
The donor AIR representation supports up to 64 bits per constant, including
QuartzCore's `uchar3` opcodes. Larger values are refused explicitly.

## Build

Use the `sweet-native-metal` GitHub Actions workflow on this branch, or macOS
with the iPhoneOS SDK, Python 3.12+, Ninja, Bison and Flex:

```
python3 tools/build-sweet-native-metal.py
```

Outputs are `userspace/out/SweetNativeMetal.bundle`,
`native-metal-build.json`, and a dependency listing. Every binary is ad-hoc
signed for iOS 16 arm64. The build report records source and ABI hashes and
explicitly marks hardware qualification false.

Source preparation can be reviewed offline on Linux using a local donor clone:

```
python3 tools/build-sweet-native-metal.py --prepare-only \
  --donor-repo /home/dan/ios-sweet/research/hackios-userland-reference \
  --work /tmp/sweet-native-source-review
```

A fresh work directory is required. The input clone is read using `git archive`
at the pinned revision; its working tree is never modified.

Kernel patch 0173 adds `sweetnative=1` to select `SweetNativeMetalDevice` from
`SweetNativeMetal.bundle`. Use it with `sweetmtl=2` for the existing aligned owned IOSurface rows.
Existing selection stays the default. The bundle
alone does not alter a running phone. Installation/boot is deliberately not part
of the build script while another developer is using the device.

## Transport contract checks

On Linux, with a Mesa source include directory:

```
bash tools/check-sweet-native-transport.sh /path/to/mesa/include
```

Five cases cover scoped residency across 16-IB chunks, mapped 64-bit kernel
fences, complete prevalidation, imported IOSurface offsets/lifetime, timeout
quarantine and partial submission failure. The IOKit/Mach implementation is a
host model; it executes no Adreno instructions and makes no hardware claim.

## Remaining requirements

Compilation is not evidence of working UI acceleration or improved frame rate.
The current compositor is substantially fill/pass bound; avoiding Vulkan alone
cannot establish a performance improvement.

This frontend is not yet a complete replacement for SweetMetal2. Outstanding
API coverage includes texture views/buffer-backed textures, heaps, blit encoders,
shared events, multisample resolve, indirect commands, non-default culling and
wireframe. AIR also has unsupported instructions/stitching cases. Some donor
SPI methods still decline features; all CoreAnimation calls and shader variants
must be audited against the active iOS 16 cache before enabling it as default.
Native function reflection is limited. Metal source-language compilation is
not provided; the driver consumes AIR/metallib.

Hardware qualification must establish real draw/compute completion, resource
visibility, surface format/row correctness, pass load/store and synchronization
semantics, then compare frame times and visual output with the current baseline.
No phone was accessed to develop this branch.

## Existing kernel compatibility package

The build also emits `userspace/out/native-legacy/SweetMetal2.bundle`.
It contains the same native frontend/backend, with an Objective-C
`SweetMTL2Device` subclass and the bundle name expected by pre-0173 kernels.
Use `sweetmtl=2 sweetgles=0` with an ABI1 kernel supporting HostImport,
SubmitResources and Compositor4096 (0172 has these paths). No kernel binary
patching is involved. This package replaces the active SweetMetal2 userspace
bundle only after backing it up; the build never installs it. The existing
Turnip-based bundle must be retained outside the active extension path for
rollback. The ordinary SweetMetal2 installer is unsuitable because it assumes
a Vulkan dependency and a different fixed file list. Runtime qualification and
the API coverage limits above still apply. Ask before booting any image.

## First hardware trial

The legacy bundle loads on 0172, but BackBoard repeatedly aborts while building
an AIR pipeline containing a pointer store. Kernel touch reports continue;
the unresponsive lock screen is observed during the compositor crash loop.
The follow-up compiler promotes non-escaping pointer locals with direct,
non-volatile loads/stores confined to the allocation block. It preserves the
stored pointer resolution and rejects uninitialized loads, escaping locals and
cross-block accesses. Compilation and the exact failing shader still need
qualification; this is not a successful native UI rendering result.

## QuartzCore compiler correction (2026-10-31)

The failing shader was reproduced offline: the generic `fixed_frag_lph_cph`
and `fixed_frag_lph_cpf` in iOS 16.7.16 / 20H392 store a sampler argument in an
entry-block alloca, cast/select its address across blocks, then load through
it. The other side of the choice is a constant global wrapping an inline
sampler pointer. The previous same-block local promotion cannot express this.

`backend/air_pointer_cells.inc` adds single-store, non-escaping pointer cells,
constant-global layout, program-constant propagation and executable CFG edges
under function constants. It preserves resource resolutions and refuses dynamic
pointer memory it cannot represent. Generic shaders' selected texture/sampler
bindings match Apple's pre-specialized pipeline modules for all 451 recorded
QuartzCore pipelines in the local corpus. This checks compiler coverage and
resource selection, not full numerical/image equivalence or A618 execution.
Four imageblock compute shaders remain unsupported.

Host comparison target: `src/compiler/air/air_specialization_check` in an adapted
Mesa build. Run it with the locally extracted QuartzCore `default.metallib`,
`default.pipelinelib/pipelines.desc`, and `default.pipelinelib/functions` as its
three arguments. Apple files are local inputs and must not be committed or
uploaded to public CI. The comparison also fails on reference translation
errors and an empty corpus. Reproducing the previous pointer-store refusal and
passing this comparison precede the next native trial.

## Specialized resource validation (2026-10-31)

The pointer-cell correction passes compilation on the phone, but its next draw
failed because AIR declarations included an unused buffer. Pipeline creation
now optimizes executable NIR and filters inactive direct resource declarations
before validation and binding. Dynamic indices retain all resources of their
kind, argument-buffer layouts remain intact, and binding numbers are unchanged.
The local 451-pipeline comparison now also checks live buffer slots. Synthetic
cases cover dead reads, retained writes, dynamic indices and texture/sampler
selection. Passing these checks does not establish correct rendered pixels;
the next hardware trial must confirm compositor output and interaction.

## Imported-memory ownership

The native transport imports task-owned cached pages for all BOs. Sweet kernels
can delegate cache maintenance of large imports to userspace. The transport
therefore cleans and invalidates each submitted BO before GPU use, waits for
prior work before changing ownership, and acquires GPU-written pages only after
a real fence completes. This uses the same EL0 `dc civac` operation as the
working SweetMetal2 frontend. LibSystem cache stubs are insufficient here.
Serialization is intentional until correct rendering is established; it is not
a performance optimization.

## Interstage semantics

Real vertex/fragment pipelines are linked by AIR's generated interstage semantic
before Gallium IO lowering. Declaration positions are independent and cannot be
used as the link key. Unused fragment inputs are removed first; live inputs need
a matching vertex output with compatible width and component count. A bounded
startup log records actual stage pairings and remapped inputs. Built-in locations
are left to Gallium. This does not replace pixel-level runtime qualification.

The Gallium handoff also gathers shader information and calls the screen's NIR
finalizer before creating shader state. Freedreno's early finalizer and its later
post-finalizer are separate passes; shader creation alone does not run both.
Finalization errors are propagated to the Metal pipeline/command error path.

## Local shader capture

Creating `/private/var/tmp/sweet-native-capture` enables bounded captures of
`fixed_` AIR functions and immutable specialization constants (up to16 per
stage/process). Files are written under `/private/var/tmp/sweet-native-*` as
`.air` and `.json`. Remove the enable file to stop capture. These Apple shader
inputs stay local and must not be committed or uploaded to public CI. Capture
records do not include application buffer contents.

## A6xx vertex exports

Explicit16-bit AIR vertex outputs are converted to32-bit when lowering output
stores for A6xx VPC. Its linkage selects full registers and does not encode the
half-register flag recorded by ir3. The conversion preserves the half value's
numeric result; it does not promote the shader's internal arithmetic or alter
fragment render-target export precision.
