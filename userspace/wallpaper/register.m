/* Targeted iOS16 wallpaper registration. Runtime selectors are checked; no
 * dyld-cache addresses or private-method instruction offsets are assumed.
 * API references: NWylynko/ios-flame phone/tools/{lsreg,exq}.m (745c4341).
 */
#import <Foundation/Foundation.h>
#include <IOKit/IOKitLib.h>
#include <dlfcn.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

@interface NSObject (SweetWallpaperSPI)
+ (id)defaultWorkspace;
+ (id)enumeratorWithOptions:(NSUInteger)options;
- (NSString *)bundleIdentifier;
- (void)registerPlugin:(NSURL *)url;
- (BOOL)registerApplication:(NSURL *)url;
- (BOOL)_LSPrivateRebuildApplicationDatabasesForSystemApps:(BOOL)s internal:(BOOL)i user:(BOOL)u;
- (id)initWithExtensionPointIdentifier:(NSString *)identifier;
+ (NSArray *)executeQuery:(id)query;
@end

static NSArray *queryProviders(void) {
    Class q = NSClassFromString(@"_EXQuery");
    Class c = NSClassFromString(@"_EXQueryController");
    if (![q instancesRespondToSelector:@selector(initWithExtensionPointIdentifier:)] ||
        ![c respondsToSelector:@selector(executeQuery:)]) return nil;
    id query = [[q alloc] initWithExtensionPointIdentifier:@"com.apple.posterkit.provider"];
    id result = [c executeQuery:query];
    return [result isKindOfClass:[NSArray class]] ? result : nil;
}

static BOOL registered(NSArray *results) {
    Class records = NSClassFromString(@"LSApplicationExtensionRecord");
    if (![records respondsToSelector:@selector(enumeratorWithOptions:)]) return NO;
    BOOL collections = NO, legacy = NO;
    for (id record in [records enumeratorWithOptions:0]) {
        if (![record respondsToSelector:@selector(bundleIdentifier)]) continue;
        NSString *identifier = [record bundleIdentifier];
        collections |= [identifier isEqualToString:@"com.apple.WallpaperKit.CollectionsPoster"];
        legacy |= [identifier isEqualToString:@"com.apple.PaperBoard.LegacyPoster"];
    }
    printf("SWEET WALLPAPER: query=%ld collections=%d legacy=%d\n",
           results ? (long)results.count : -1L, collections, legacy);
    return results.count > 0 && collections && legacy;
}

static BOOL rebuildPrerequisites(void) {
    io_service_t service = IOServiceGetMatchingService(kIOMainPortDefault,
                            IOServiceMatching("AppleMobileFileIntegrity"));
    if (!service) { puts("SWEET WALLPAPER: rebuild refused: AMFI service absent"); return NO; }
    IOObjectRelease(service);
    io_registry_entry_t chosen = IORegistryEntryFromPath(kIOMainPortDefault, "IODeviceTree:/chosen");
    if (!chosen) return NO;
    BOOL valid = YES;
    for (NSString *key in @[@"mac-address-wifi0", @"mac-address-bluetooth0", @"mac-address-ethernet0"]) {
        CFTypeRef value = IORegistryEntryCreateCFProperty(chosen, (__bridge CFStringRef)key,
                                                        kCFAllocatorDefault, 0);
        valid &= value && CFGetTypeID(value) == CFDataGetTypeID() &&
                 CFDataGetLength((CFDataRef)value) == 6;
        if (value) CFRelease(value);
    }
    IOObjectRelease(chosen);
    if (!valid) puts("SWEET WALLPAPER: rebuild refused: chosen MAC identities absent");
    return valid;
}

static int rebuildOnce(void) {
    if (!rebuildPrerequisites()) return 9;
    if (registered(queryProviders())) {
        puts("SWEET WALLPAPER: providers already verified; rebuild skipped"); return 0;
    }
    NSString *marker = @"/private/var/db/sweet-wallpaper-rebuild-attempt.json";
    if ([[NSFileManager defaultManager] fileExistsAtPath:marker]) {
        puts("SWEET WALLPAPER: previous rebuild attempt recorded; refusing automatic retry"); return 8;
    }
    Class cls = NSClassFromString(@"LSApplicationWorkspace");
    if (![cls respondsToSelector:@selector(defaultWorkspace)]) return 5;
    id ws = [cls defaultWorkspace];
    if (![ws respondsToSelector:@selector(_LSPrivateRebuildApplicationDatabasesForSystemApps:internal:user:)]) return 6;
    NSData *attempt = [NSJSONSerialization dataWithJSONObject:@{@"state": @"attempting",
                         @"version": @1} options:0 error:nil];
    if (![attempt writeToFile:marker options:NSDataWritingAtomic error:nil]) return 10;
    puts("SWEET WALLPAPER: starting one-time LaunchServices rebuild with prerequisites present");
    BOOL ok = [ws _LSPrivateRebuildApplicationDatabasesForSystemApps:YES internal:YES user:YES];
    printf("SWEET WALLPAPER: LaunchServices rebuild returned=%d\n", ok);
    if (!ok) return 7;
    for (unsigned i = 0; i < 8; ++i) {
        if (registered(queryProviders())) {
            NSData *success = [NSJSONSerialization dataWithJSONObject:@{@"state": @"verified",
                                 @"version": @1} options:0 error:nil];
            if (![success writeToFile:marker options:NSDataWritingAtomic error:nil]) return 10;
            puts("SWEET WALLPAPER: VERIFIED after one-time system database rebuild"); return 0;
        }
        sleep(2);
    }
    puts("SWEET WALLPAPER: rebuild returned but providers remain unverified"); return 7;
}

int main(int argc, char **argv) {
    @autoreleasepool {
        /* Registration can block LaunchServices during early bootstrap. Never
         * mutate its database merely because this executable was launched.
         * --register is reserved for an explicitly controlled diagnostic run;
         * the installed launch job must remain Disabled after the 0121 stall.
         */
        BOOL repair = argc == 2 && strcmp(argv[1], "--register") == 0;
        BOOL rebuild = argc == 2 && strcmp(argv[1], "--rebuild-once") == 0;
        if (argc > 1 && !repair && !rebuild) return 1;
        if (getuid() != (uid_t)(rebuild ? 0 : 501)) return 2;
        alarm(rebuild ? 300 : 90);
        freopen("/private/var/mobile/Library/Logs/sweet-wallpaper.log", "a", stdout);
        setvbuf(stdout, NULL, _IOLBF, 0);
        printf("SWEET WALLPAPER: start uid=%d\n", getuid());
        if (!dlopen("/System/Library/Frameworks/CoreServices.framework/CoreServices", RTLD_NOW) ||
            !dlopen("/System/Library/Frameworks/ExtensionFoundation.framework/ExtensionFoundation", RTLD_NOW)) {
            puts("SWEET WALLPAPER: required framework unavailable"); return 3;
        }
        if (rebuild) {
            @try { return rebuildOnce(); }
            @catch (NSException *error) {
                printf("SWEET WALLPAPER: rebuild exception %s\n", error.description.UTF8String);
                return 7;
            }
        }
        if (!repair) {
            @try {
                BOOL ok = registered(queryProviders());
                puts("SWEET WALLPAPER: query-only; no registration requested");
                return ok ? 0 : 7;
            } @catch (NSException *error) {
                printf("SWEET WALLPAPER: query exception %s\n", error.description.UTF8String);
                return 7;
            }
        }
        NSArray *paths = [NSArray arrayWithContentsOfFile:@"/usr/local/share/sweet-wallpaper/providers.plist"];
        if (!paths.count || paths.count > 64) return 4;
        /* Bounded attempts: failed registrations are retried on the next boot,
         * without a KeepAlive crash loop or a success marker based on SPI return. */
        for (unsigned attempt = 0; attempt < 8; ++attempt) {
            @try {
                if (registered(queryProviders())) {
                    puts("SWEET WALLPAPER: VERIFIED provider records and ExtensionFoundation query");
                    return 0;
                }
                Class cls = NSClassFromString(@"LSApplicationWorkspace");
                if (![cls respondsToSelector:@selector(defaultWorkspace)]) return 5;
                id ws = [cls defaultWorkspace];
                if (![ws respondsToSelector:@selector(registerPlugin:)] ||
                    ![ws respondsToSelector:@selector(registerApplication:)]) return 6;
                NSString *host = @"/Applications/PaperBoard.app";
                if ([[NSFileManager defaultManager] fileExistsAtPath:host])
                    printf("SWEET WALLPAPER: PaperBoard registration=%d\n",
                           [ws registerApplication:[NSURL fileURLWithPath:host]]);
                for (NSString *path in paths) {
                    if (![path isKindOfClass:[NSString class]] ||
                        (![path hasPrefix:@"/System/Library/"] && ![path hasPrefix:@"/Applications/"]) ||
                        ![path hasSuffix:@".appex"] || [path containsString:@".."] ||
                        ![[NSFileManager defaultManager] fileExistsAtPath:path]) continue;
                    [ws registerPlugin:[NSURL fileURLWithPath:path]];
                    printf("SWEET WALLPAPER: requested %s\n", path.UTF8String);
                }
            } @catch (NSException *error) {
                printf("SWEET WALLPAPER: exception %s\n", error.description.UTF8String);
            }
            sleep(5);
        }
        @try {
            if (registered(queryProviders())) {
                puts("SWEET WALLPAPER: VERIFIED provider records and ExtensionFoundation query");
                return 0;
            }
        } @catch (NSException *error) {
            printf("SWEET WALLPAPER: final query exception %s\n", error.description.UTF8String);
        }
        puts("SWEET WALLPAPER: registration remains unverified; no success recorded");
        return 7;
    }
}
