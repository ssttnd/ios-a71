/*
 * sweetdbg: remote debugging for the A71 over the home Wi-Fi (TCP 7777). One command per connection; the client is
 * tools/a71-dbg.py. Root, started by wland (0029). Every connection must start with the key from
 * /var/local/sweetdbg.key (made 0600 here); a wrong key closes the connection.
 *
 * Request: one line, fields separated by TAB: KEY \t COMMAND \t ARG...
 *   run   PATH ARG...        spawn, stdout+stderr streamed back, then "\n[exit N]\n" (killed after 120 s)
 *   runbg PATH ARG...        a daemon in its own session, output to /dev/null; "OK pid N"
 *   mv    FROM TO
 *   get   PATH               "OK size\n" + bytes
 *   put   PATH MODE SIZE     then SIZE bytes; written to PATH.sdtmp, renamed, chmod MODE (octal); "OK\n"
 *   ls    PATH               one line per entry: type mode size name
 *   rm    PATH | mkdir PATH
 *   ps                       pid ppid uid name
 *   kill  PID SIG
 *   shot                     "OK bytes width height stride\n" + BGRA rows of the frame being scanned out
 *   tap   X Y                touch down/up at panel pixels (1080x2400), needs kernel 0209
 *   swipe X1 Y1 X2 Y2 MS     touch moved in 16 ms steps
 *   key   N [MS]             0 power, 1 volume up, 2 volume down, 3 home; held MS (default 100)
 *   rd    PA [COUNT]         32-bit words through /dev/pmem
 *   wr    PA VALUE           one 32-bit word through /dev/pmem
 *   sysctl NAME              value as text (int, string or hex)
 *   restart                  exec /var/local/bin/sweetdbg again (after a put of a new one)
 */
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <pthread.h>
#include <signal.h>
#include <spawn.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/sysctl.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

extern char **environ;

#define PORT		7777
#define KEY_PATH	"/var/local/sweetdbg.key"
#define SELF		"/var/local/bin/sweetdbg"
#define MDP_PA		0x0ae00000ull
#define VIG0_SIZE	(MDP_PA + 0x5000)
#define VIG0_ADDR	(MDP_PA + 0x5014)
#define VIG0_STRIDE	(MDP_PA + 0x5024)

struct pmem_inject {
	int32_t kind, a, b, c, d;
};
#define PMEM_INJECT	_IOW('P', 7, struct pmem_inject)
struct pmem_smc {
	uint64_t x[8];
};
#define PMEM_SMC	_IOWR('P', 3, struct pmem_smc)
#define IMEM_RESTART_REASON	0x146aa65cull	/* DT qcom,msm-imem@146aa000 restart_reason@65c */
#define RESTART_RECOVERY	0x77665502u	/* what Linux on Qualcomm writes for "reboot recovery" */
#define PSCI_SYSTEM_RESET	0x84000009u
/* SPMI PMIC arbiter v5 (DT qcom,spmi@c440000: core, chnls, obsrvr, intr, cnfg) */
#define SPMI_CORE	0x0c440000ull
#define SPMI_CHNLS	0x0c600000ull	/* read/write channel of APID n at + 0x10000 * n */
#define SPMI_CNFG	0x0c40a000ull
#define PON_SOFT_RB_SPARE	0x88f	/* PM6150 (SID 0) PON + 0x8f: restart reason in bits 7:1, recovery = 1 */
static int pm_fd(void);
static int pm_rd32(uint64_t pa, uint32_t *v);

static int spmi_apid(unsigned sid, unsigned addr, int *owned)
{
	uint32_t n = 0;
	if (pm_rd32(SPMI_CORE + 4, &n))
		return -1;
	n &= 0x7ff;
	unsigned ppid = (sid << 8) | (addr >> 8);
	int found = -1;
	for (unsigned i = 0; i < n && i < 512; i++) {
		uint32_t map = 0, own = 7;
		if (pm_rd32(SPMI_CORE + 0x900 + 4ull * i, &map) || !map || ((map >> 8) & 0xfff) != ppid)
			continue;
		pm_rd32(SPMI_CNFG + 0x700 + 4ull * i, &own);
		if (found < 0 || (own & 7) == 0) {
			found = (int)i;
			*owned = (own & 7) == 0;
		}
	}
	return found;
}

/* one byte; write = 1 only on an APID the apps EE owns. 0 or an errno */
static int spmi_xfer(unsigned sid, unsigned addr, int write, uint8_t *val)
{
	int owned = 0, apid = spmi_apid(sid, addr, &owned);
	if (apid < 0)
		return ENOENT;
	if (write && !owned)
		return EPERM;
	uint64_t ch = SPMI_CHNLS + 0x10000ull * (unsigned)apid;
	if (write) {
		uint32_t d = *val;
		if (pwrite(pm_fd(), &d, 4, (off_t)(ch + 0x10)) != 4)
			return EIO;
	}
	uint32_t cmd = ((write ? 0u : 1u) << 27) | ((addr & 0xffu) << 4);
	if (pwrite(pm_fd(), &cmd, 4, (off_t)ch) != 4)
		return EIO;
	for (int i = 0; i < 1000; i++) {
		uint32_t st = 0;
		pm_rd32(ch + 0x08, &st);
		if (st & 1) {
			if (st & 0x0e)
				return EIO;
			if (!write) {
				uint32_t d = 0;
				pm_rd32(ch + 0x18, &d);
				*val = (uint8_t)d;
			}
			return 0;
		}
		usleep(10);
	}
	return ETIMEDOUT;
}

static char key[129];
static int listen_fd = -1;

static void say(int fd, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
static void say(int fd, const char *fmt, ...)
{
	char b[1024];
	va_list ap;
	va_start(ap, fmt);
	int n = vsnprintf(b, sizeof(b), fmt, ap);
	va_end(ap);
	if (n > (int)sizeof(b) - 1)
		n = sizeof(b) - 1;
	for (int o = 0; o < n;) {
		ssize_t w = write(fd, b + o, n - o);
		if (w <= 0)
			return;
		o += w;
	}
}

static int send_all(int fd, const void *p, size_t n)
{
	const uint8_t *b = p;
	while (n) {
		ssize_t w = write(fd, b, n);
		if (w <= 0)
			return -1;
		b += w, n -= w;
	}
	return 0;
}

static int recv_all(int fd, void *p, size_t n)
{
	uint8_t *b = p;
	while (n) {
		ssize_t r = read(fd, b, n);
		if (r <= 0)
			return -1;
		b += r, n -= r;
	}
	return 0;
}

static int pm_fd(void)
{
	static int fd = -2;
	if (fd == -2)
		fd = open("/dev/pmem", O_RDWR);
	return fd;
}

static int pm_rd32(uint64_t pa, uint32_t *v)
{
	return pread(pm_fd(), v, 4, (off_t)pa) == 4 ? 0 : -1;
}

static int inject(int kind, int a, int b, int c, int d)
{
	struct pmem_inject in = { kind, a, b, c, d };
	return ioctl(pm_fd(), PMEM_INJECT, &in) ? errno : 0;
}

static void ms_sleep(unsigned ms)
{
	usleep(ms * 1000);
}

static void cmd_run(int fd, char **av, int ac)
{
	if (ac < 1) {
		say(fd, "ERR run needs a path\n");
		return;
	}
	int p[2];
	if (pipe(p)) {
		say(fd, "ERR pipe: %s\n", strerror(errno));
		return;
	}
	posix_spawn_file_actions_t fa;
	posix_spawn_file_actions_init(&fa);
	posix_spawn_file_actions_addopen(&fa, 0, "/dev/null", O_RDONLY, 0);
	posix_spawn_file_actions_adddup2(&fa, p[1], 1);
	posix_spawn_file_actions_adddup2(&fa, p[1], 2);
	posix_spawn_file_actions_addclose(&fa, p[0]);
	char *argv[64];
	int n = 0;
	for (; n < ac && n < 63; n++)
		argv[n] = av[n];
	argv[n] = NULL;
	pid_t pid;
	int e = posix_spawn(&pid, argv[0], &fa, NULL, argv, environ);
	posix_spawn_file_actions_destroy(&fa);
	close(p[1]);
	if (e) {
		close(p[0]);
		say(fd, "ERR spawn %s: %s\n", argv[0], strerror(e));
		return;
	}
	time_t end = time(NULL) + 120;
	char buf[4096];
	fcntl(p[0], F_SETFL, O_NONBLOCK);
	int st = 0, done = 0;
	for (;;) {
		ssize_t r = read(p[0], buf, sizeof(buf));
		if (r > 0) {
			if (send_all(fd, buf, r))
				break;
			continue;
		}
		if (r == 0)
			break;
		if (errno != EAGAIN)
			break;
		if (!done && waitpid(pid, &st, WNOHANG) == pid)
			done = 1;
		if (time(NULL) > end) {
			kill(pid, SIGKILL);
			say(fd, "\n[killed after 120 s]\n");
			break;
		}
		ms_sleep(20);
	}
	close(p[0]);
	if (!done)
		waitpid(pid, &st, 0);
	say(fd, "\n[exit %d%s%d]\n", WIFEXITED(st) ? WEXITSTATUS(st) : -1, WIFSIGNALED(st) ? " signal " : "",
	    WIFSIGNALED(st) ? WTERMSIG(st) : 0);
}

static void *reap_child(void *arg)
{
	pid_t pid = (pid_t)(intptr_t)arg;
	int st = 0;
	waitpid(pid, &st, 0);
	printf("sweetdbg: pid %d ended, status %#x\n", pid, st);
	return NULL;
}

/* runbg PATH ARG...: a daemon: its own session, stdout and stderr to /dev/null (the console, with sweetnull=NAME) */
static void cmd_runbg(int fd, char **av, int ac)
{
	if (ac < 1) {
		say(fd, "ERR runbg needs a path\n");
		return;
	}
	posix_spawn_file_actions_t fa;
	posix_spawnattr_t at;
	posix_spawn_file_actions_init(&fa);
	posix_spawn_file_actions_addopen(&fa, 0, "/dev/null", O_RDONLY, 0);
	posix_spawn_file_actions_addopen(&fa, 1, "/dev/null", O_WRONLY, 0);
	posix_spawn_file_actions_addopen(&fa, 2, "/dev/null", O_WRONLY, 0);
	posix_spawnattr_init(&at);
	posix_spawnattr_setflags(&at, POSIX_SPAWN_SETSID);
	char *argv[64];
	int n = 0;
	for (; n < ac && n < 63; n++)
		argv[n] = av[n];
	argv[n] = NULL;
	pid_t pid = -1;
	int e = posix_spawn(&pid, argv[0], &fa, &at, argv, environ);
	posix_spawn_file_actions_destroy(&fa);
	posix_spawnattr_destroy(&at);
	if (e) {
		say(fd, "ERR spawn %s: %s\n", argv[0], strerror(e));
		return;
	}
	pthread_t t;
	if (!pthread_create(&t, NULL, reap_child, (void *)(intptr_t)pid))
		pthread_detach(t);
	say(fd, "OK pid %d\n", pid);
}

static void cmd_get(int fd, const char *path)
{
	int f = open(path, O_RDONLY);
	struct stat s;
	if (f < 0 || fstat(f, &s)) {
		say(fd, "ERR %s: %s\n", path, strerror(errno));
		if (f >= 0)
			close(f);
		return;
	}
	say(fd, "OK %lld\n", (long long)s.st_size);
	char buf[65536];
	ssize_t r;
	while ((r = read(f, buf, sizeof(buf))) > 0)
		if (send_all(fd, buf, r))
			break;
	close(f);
}

static void cmd_put(int fd, const char *path, const char *mode, const char *size)
{
	long long n = atoll(size);
	char tmp[1100];
	snprintf(tmp, sizeof(tmp), "%s.sdtmp", path);
	int f = open(tmp, O_WRONLY | O_CREAT | O_TRUNC, 0600);
	if (f < 0 || n < 0) {
		say(fd, "ERR %s: %s\n", tmp, strerror(errno));
		return;
	}
	char buf[65536];
	while (n > 0) {
		ssize_t r = read(fd, buf, n < (long long)sizeof(buf) ? (size_t)n : sizeof(buf));
		if (r <= 0 || write(f, buf, r) != r) {
			close(f);
			unlink(tmp);
			say(fd, "ERR short transfer\n");
			return;
		}
		n -= r;
	}
	fsync(f);
	close(f);
	chmod(tmp, (mode_t)strtol(mode, NULL, 8));
	if (rename(tmp, path)) {
		say(fd, "ERR rename: %s\n", strerror(errno));
		unlink(tmp);
		return;
	}
	say(fd, "OK\n");
}

static void cmd_ls(int fd, const char *path)
{
	DIR *d = opendir(path);
	if (!d) {
		say(fd, "ERR %s: %s\n", path, strerror(errno));
		return;
	}
	struct dirent *e;
	while ((e = readdir(d))) {
		char p[2048];
		struct stat s;
		snprintf(p, sizeof(p), "%s/%s", path, e->d_name);
		if (lstat(p, &s)) {
			say(fd, "? ? ? %s\n", e->d_name);
			continue;
		}
		char t = S_ISDIR(s.st_mode) ? 'd' : S_ISLNK(s.st_mode) ? 'l' : S_ISREG(s.st_mode) ? 'f' : 'o';
		say(fd, "%c %04o %lld %s\n", t, s.st_mode & 07777, (long long)s.st_size, e->d_name);
	}
	closedir(d);
}

static void cmd_ps(int fd)
{
	int mib[3] = { CTL_KERN, KERN_PROC, KERN_PROC_ALL };
	size_t len = 0;
	if (sysctl(mib, 3, NULL, &len, NULL, 0)) {
		say(fd, "ERR sysctl: %s\n", strerror(errno));
		return;
	}
	len += 64 * sizeof(struct kinfo_proc);
	struct kinfo_proc *kp = malloc(len);
	if (!kp || sysctl(mib, 3, kp, &len, NULL, 0)) {
		say(fd, "ERR sysctl: %s\n", strerror(errno));
		free(kp);
		return;
	}
	for (size_t i = 0; i < len / sizeof(*kp); i++)
		say(fd, "%d %d %u %s\n", kp[i].kp_proc.p_pid, kp[i].kp_eproc.e_ppid, kp[i].kp_eproc.e_ucred.cr_uid,
		    kp[i].kp_proc.p_comm);
	free(kp);
}

static void cmd_shot(int fd)
{
	uint32_t size = 0, addr = 0, stride = 0;
	if (pm_fd() < 0 || pm_rd32(VIG0_SIZE, &size) || pm_rd32(VIG0_ADDR, &addr) || pm_rd32(VIG0_STRIDE, &stride)) {
		say(fd, "ERR display registers: %s\n", strerror(errno));
		return;
	}
	uint32_t w = size & 0xffff, h = size >> 16;
	stride &= 0xffff;
	if (!w || !h || stride < w * 4 || !addr) {
		say(fd, "ERR odd scanout: size %#x addr %#x stride %#x\n", size, addr, stride);
		return;
	}
	say(fd, "OK %u %u %u %u\n", h * stride, w, h, stride);
	static uint8_t row[16384];
	for (uint32_t y = 0; y < h; y++) {
		if (pread(pm_fd(), row, stride, (off_t)addr + (off_t)y * stride) != (ssize_t)stride)
			memset(row, 0, stride);
		if (send_all(fd, row, stride))
			return;
	}
}

static void cmd_sysctl(int fd, const char *name)
{
	uint8_t buf[4096];
	size_t len = sizeof(buf);
	if (sysctlbyname(name, buf, &len, NULL, 0)) {
		say(fd, "ERR %s: %s\n", name, strerror(errno));
		return;
	}
	if (len == 4) {
		say(fd, "%d\n", *(int32_t *)buf);
	} else if (len == 8) {
		say(fd, "%lld\n", *(long long *)buf);
	} else {
		int text = len > 0;
		for (size_t i = 0; i + 1 < len; i++)
			if (buf[i] < 9 || buf[i] > 126)
				text = 0;
		if (text) {
			buf[len - 1] = 0;
			say(fd, "%s\n", buf);
		} else {
			for (size_t i = 0; i < len; i++)
				say(fd, "%02x%s", buf[i], (i % 32 == 31 || i + 1 == len) ? "\n" : "");
		}
	}
}

static void handle(int fd)
{
	char line[4096];
	size_t n = 0;
	while (n < sizeof(line) - 1) {
		ssize_t r = read(fd, line + n, 1);
		if (r <= 0)
			return;
		if (line[n] == '\n')
			break;
		n++;
	}
	line[n] = 0;
	char *av[80];
	int ac = 0;
	for (char *s = line, *t; s && ac < 79; s = t) {
		t = strchr(s, '\t');
		if (t)
			*t++ = 0;
		av[ac++] = s;
	}
	if (ac < 2 || !key[0] || strcmp(av[0], key)) {
		ms_sleep(500);
		say(fd, "ERR key\n");
		return;
	}
	const char *c = av[1];
	char **a = av + 2;
	int na = ac - 2;
	printf("sweetdbg: %s%s%s\n", c, na ? " " : "", na ? a[0] : "");
	if (!strcmp(c, "run"))
		cmd_run(fd, a, na);
	else if (!strcmp(c, "runbg"))
		cmd_runbg(fd, a, na);
	else if (!strcmp(c, "mv") && na == 2) {
		if (rename(a[0], a[1]))
			say(fd, "ERR %s\n", strerror(errno));
		else
			say(fd, "OK\n");
	}
	else if (!strcmp(c, "get") && na == 1)
		cmd_get(fd, a[0]);
	else if (!strcmp(c, "put") && na == 3)
		cmd_put(fd, a[0], a[1], a[2]);
	else if (!strcmp(c, "ls") && na == 1)
		cmd_ls(fd, a[0]);
	else if (!strcmp(c, "rm") && na == 1)
		say(fd, unlink(a[0]) && rmdir(a[0]) ? "ERR %s\n" : "OK\n", strerror(errno));
	else if (!strcmp(c, "mkdir") && na == 1)
		say(fd, mkdir(a[0], 0755) ? "ERR %s\n" : "OK\n", strerror(errno));
	else if (!strcmp(c, "ps"))
		cmd_ps(fd);
	else if (!strcmp(c, "kill") && na == 2)
		say(fd, kill(atoi(a[0]), atoi(a[1])) ? "ERR %s\n" : "OK\n", strerror(errno));
	else if (!strcmp(c, "shot"))
		cmd_shot(fd);
	else if (!strcmp(c, "tap") && na == 2) {
		int e = inject(0, 0, 1, atoi(a[0]), atoi(a[1]));
		ms_sleep(60);
		e = e ? e : inject(0, 0, 0, atoi(a[0]), atoi(a[1]));
		say(fd, e ? "ERR inject: %s (kernel 0209?)\n" : "OK\n", strerror(e));
	} else if (!strcmp(c, "swipe") && na == 5) {
		int x1 = atoi(a[0]), y1 = atoi(a[1]), x2 = atoi(a[2]), y2 = atoi(a[3]), ms = atoi(a[4]);
		int steps = ms / 16 < 2 ? 2 : ms / 16, e = 0;
		for (int i = 0; i <= steps && !e; i++) {
			e = inject(0, 0, 1, x1 + (x2 - x1) * i / steps, y1 + (y2 - y1) * i / steps);
			ms_sleep(16);
		}
		e = e ? e : inject(0, 0, 0, x2, y2);
		say(fd, e ? "ERR inject: %s (kernel 0209?)\n" : "OK\n", strerror(e));
	} else if (!strcmp(c, "key") && (na == 1 || na == 2)) {
		int e = inject(1, atoi(a[0]), 1, 0, 0);
		ms_sleep(na == 2 ? (unsigned)atoi(a[1]) : 100);
		e = e ? e : inject(1, atoi(a[0]), 0, 0, 0);
		say(fd, e ? "ERR inject: %s (kernel 0209?)\n" : "OK\n", strerror(e));
	} else if (!strcmp(c, "rd") && (na == 1 || na == 2)) {
		uint64_t pa = strtoull(a[0], NULL, 0);
		int cnt = na == 2 ? atoi(a[1]) : 1;
		for (int i = 0; i < cnt && i < 4096; i++) {
			uint32_t v;
			if (pm_rd32(pa + 4ull * i, &v)) {
				say(fd, "ERR read %#llx: %s\n", (unsigned long long)(pa + 4ull * i), strerror(errno));
				break;
			}
			say(fd, "%s%08x%s", i % 8 ? " " : "", v, (i % 8 == 7 || i + 1 == cnt) ? "\n" : "");
			if (i % 8 == 7 && i + 1 < cnt)
				say(fd, "%#llx: ", (unsigned long long)(pa + 4ull * (i + 1)));
		}
	} else if (!strcmp(c, "wr") && na == 2) {
		uint32_t v = (uint32_t)strtoul(a[1], NULL, 0);
		say(fd, pwrite(pm_fd(), &v, 4, (off_t)strtoull(a[0], NULL, 0)) == 4 ? "OK\n" : "ERR %s\n", strerror(errno));
	} else if (!strcmp(c, "reboot") && na <= 1) {
		/* reboot [recovery]: restart reason in IMEM (recovery only), sync, PSCI SYSTEM_RESET (as wland 0013) */
		int rec = na == 1 && !strcmp(a[0], "recovery");
		uint32_t v = rec ? RESTART_RECOVERY : 0;
		if (pwrite(pm_fd(), &v, 4, (off_t)IMEM_RESTART_REASON) != 4) {
			say(fd, "ERR restart reason: %s\n", strerror(errno));
			return;
		}
		uint32_t back = 0;
		pm_rd32(IMEM_RESTART_REASON, &back);
		/* the PMIC's copy, which the bootloader reads on newer Qualcomm parts */
		uint8_t sp = 0;
		int e1 = spmi_xfer(0, PON_SOFT_RB_SPARE, 0, &sp);
		uint8_t nv = (uint8_t)((sp & 1) | (rec ? 1u << 1 : 0));
		int e2 = e1 ? e1 : spmi_xfer(0, PON_SOFT_RB_SPARE, 1, &nv);
		uint8_t sp2 = 0;
		spmi_xfer(0, PON_SOFT_RB_SPARE, 0, &sp2);
		say(fd, "OK restart reason %#x; PON spare %#x -> %#x (%s), syncing and resetting\n", back, sp, sp2,
		    e2 ? strerror(e2) : "written");
		close(fd);
		sync();
		sleep(1);
		sync();
		struct pmem_smc s = { .x = { PSCI_SYSTEM_RESET } };
		ioctl(pm_fd(), PMEM_SMC, &s);
		printf("sweetdbg: PSCI reset returned %#llx\n", (unsigned long long)s.x[0]);
		return;
	} else if (!strcmp(c, "bars") && na == 2) {
		/* bars LOW HIGH: RSSI_BOUNDS in dBm (kernel 0215) */
		int e = inject(2, atoi(a[0]), atoi(a[1]), 0, 0);
		if (e)
			say(fd, "ERR %s\n", strerror(e));
		else
			say(fd, "OK\n");
	} else if (!strcmp(c, "spmi") && (na == 3 || na == 4)) {
		/* spmi r SID ADDR | spmi w SID ADDR VALUE (owned channels only) */
		int w = !strcmp(a[0], "w");
		uint8_t v = na == 4 ? (uint8_t)strtoul(a[3], NULL, 0) : 0;
		int e = spmi_xfer((unsigned)strtoul(a[1], NULL, 0), (unsigned)strtoul(a[2], NULL, 0), w, &v);
		if (e)
			say(fd, "ERR %s\n", strerror(e));
		else
			say(fd, "OK %#x\n", v);
	} else if (!strcmp(c, "sysctl") && na == 1)
		cmd_sysctl(fd, a[0]);
	else if (!strcmp(c, "restart")) {
		say(fd, "OK restarting\n");
		close(fd);
		close(listen_fd);
		char *argv[] = { SELF, NULL };
		execve(SELF, argv, environ);
		_exit(1);
	} else
		say(fd, "ERR unknown command or arguments: %s (%d args)\n", c, na);
}

static void *conn(void *arg)
{
	int fd = (int)(intptr_t)arg;
	handle(fd);
	close(fd);
	return NULL;
}

static void load_key(void)
{
	chmod(KEY_PATH, 0600);
	int f = open(KEY_PATH, O_RDONLY);
	ssize_t n = f >= 0 ? read(f, key, sizeof(key) - 1) : -1;
	if (f >= 0)
		close(f);
	key[n > 0 ? n : 0] = 0;
	for (char *p = key; *p; p++)
		if (*p == '\n' || *p == '\r' || *p == ' ' || *p == '\t')
			*p = 0;
	if (strlen(key) < 16) {
		key[0] = 0;
		printf("sweetdbg: no usable key in %s: every request is refused\n", KEY_PATH);
	}
}

int main(void)
{
	signal(SIGPIPE, SIG_IGN);
	setsid();
	load_key();
	listen_fd = socket(AF_INET, SOCK_STREAM, 0);
	int one = 1;
	setsockopt(listen_fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
	struct sockaddr_in sa = { .sin_len = sizeof(sa), .sin_family = AF_INET, .sin_port = htons(PORT) };
	for (int i = 0; bind(listen_fd, (struct sockaddr *)&sa, sizeof(sa)); i++) {
		if (i == 30) {
			printf("sweetdbg: bind %d: %s\n", PORT, strerror(errno));
			return 1;
		}
		sleep(1);
	}
	listen(listen_fd, 8);
	printf("sweetdbg: listening on %d (pid %d)\n", PORT, getpid());
	fflush(stdout);
	for (;;) {
		int fd = accept(listen_fd, NULL, NULL);
		if (fd < 0) {
			if (errno == EINTR)
				continue;
			ms_sleep(100);
			continue;
		}
		setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
		pthread_t t;
		if (pthread_create(&t, NULL, conn, (void *)(intptr_t)fd)) {
			close(fd);
			continue;
		}
		pthread_detach(t);
	}
}
