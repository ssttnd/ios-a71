# iOS16 wallpaper registration

## 0121 startup regression: disable automatic registration

The initial helper was deployed on 2026-10-24. The phone remained at the Apple
logo. Its saved log shows query=0, CollectionsPoster=0, LegacyPoster=0, successful
PaperBoard app registration, then no return from the first plugin registration.
SpringBoard samples show CoreServices lock waits and a synchronous XPC request;
BackBoard is idle in its event loop. This implicates startup registration, but
disabling the job and booting again is needed to establish causality.

Keep the launch job **Disabled=true, RunAtLoad=false** in both its standalone
plist and the merged launchd dictionary. A staged recovery change does exactly
that while retaining the image conversion and ExtensionKit flag. The installed
helper binary remains the original build; disabling the job is essential.

The updated source defaults to query-only. Mutating registration requires the
explicit `--register` argument and remains unsuitable for automatic early boot.
The alarm bounds the helper process only; it cannot cancel work already running
inside LaunchServices/installd. Do not claim the registration path is validated.

`register.m` attempts targeted PosterKit provider registration, using checked Objective-C
selectors from the iOS16 LaunchServices/ExtensionFoundation interfaces. The API
reference is [ios-flame's phone tools](https://github.com/NWylynko/ios-flame/tree/745c4341af86089efb0df851fa8a27fb8face624/phone/tools).
It does not use that reference's hardcoded instruction-offset call.

The intended mobile-context diagnostic mode has a 90-second alarm and
at most eight registration attempts. It registers PaperBoard and the installed
`com.apple.posterkit.provider` bundles supplied in
`/usr/local/share/sweet-wallpaper/providers.plist`. It checks that both
CollectionsPoster and LegacyPoster exist in LaunchServices and that the
ExtensionFoundation provider query is nonempty. SPI return values alone never
count as success. Diagnostics append to
`/private/var/mobile/Library/Logs/sweet-wallpaper.log`.

Build with the `sweet-wallpaper` GitHub workflow. Deploy the signed executable
as `/usr/local/libexec/sweet-wallpaper-register` (root:wheel,0755). The disabled launch job
uses `UserName=mobile`, `GroupName=mobile`, `RunAtLoad=false`, `Disabled=true`, `ProcessType=Background`,
and no `KeepAlive` or launchd stdout/stderr paths. Add the same job to the merged
`/System/Library/xpc/launchd.plist` LaunchDaemons dictionary as well as its standalone
plist. iOS16 uses the merged dictionary during bootstrap.

The root also needs Sailfish's ExtensionKit
`enforce_xpc_cache_code_signing.Enabled=false` override because these ports lack
trust-cache coverage for extension-point definitions. Preserve any other feature
flags. Do not remove existing migration preferences or trigger full migration.

Prepare wallpaper images separately with `tools/prepare-sweet-wallpapers.py`.
Keep the HEIC originals, install the validated PNG twins and updated CAML files,
and verify all original source hashes against the current root first. Registration
and PNG conversion address separate failures. The resulting wallpaper rendering
still needs observation on the device; compilation and registration are not proof
that wallpaper is visible.

For raw userdata installation, retain every changed original block, verify a
local filesystem overlay, freshly compare device blocks, obtain the approval
required by AGENTS.md, and verify every written chunk by reading it back.

## New prerequisite-gated rebuild mode (not installed or hardware verified)

`--rebuild-once` runs as root and first requires the AMFI IOService and all three
chosen MAC identities. If providers already exist, it returns without rebuilding.
Otherwise it calls the same one-time LaunchServices database rebuild documented
by the sunfish port, then requires both provider records and a nonempty EXQuery.
This does not call the old per-plugin registration loop.

A persistent attempt record in /private/var/db/sweet-wallpaper-rebuild-attempt.json
is written before the call. An incomplete attempt is not automatically repeated,
including across boots. Success is recorded only after provider queries pass.
The process alarm still cannot cancel an operation already running inside lsd.

Deployment must retain the old registration job disabled and use a distinct
root job with StartInterval=180, RunAtLoad=false and no KeepAlive. This schedules
the one-time rebuild after initial UI bootstrap. It needs the new kernel's
`sweetamfi=1` compatibility service and updated ADT; without either it refuses
mutation. Source preparation/build checks are not on-device validation.
