/*
 * iobench: read-only storage benchmark for the A71 (run through sweetdbg). Reads a big existing file with the buffer cache
 * bypassed (F_NOCACHE), sequentially in 1 MiB blocks and at random offsets in 4 KiB and 16 KiB blocks, and reports the
 * rate, the latency (mean and 99th percentile) and how much CPU the waiting cost (system time against wall time).
 *
 *   iobench [FILE]    default: the largest dyld shared cache file
 */
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

static double now_us(void)
{
	struct timespec t;
	clock_gettime(CLOCK_MONOTONIC, &t);
	return t.tv_sec * 1e6 + t.tv_nsec / 1e3;
}

static double sys_us(void)
{
	struct rusage r;
	getrusage(RUSAGE_SELF, &r);
	return r.ru_stime.tv_sec * 1e6 + r.ru_stime.tv_usec;
}

static int cmp(const void *a, const void *b)
{
	double x = *(const double *)a, y = *(const double *)b;
	return x < y ? -1 : x > y;
}

static uint64_t rng = 0x9e3779b97f4a7c15ull;
static uint64_t next_rand(void)
{
	rng ^= rng << 13;
	rng ^= rng >> 7;
	rng ^= rng << 17;
	return rng;
}

static void random_test(int fd, off_t size, size_t block, int ops)
{
	char *buf = aligned_alloc(16384, 1 << 20);
	double *lat = malloc(sizeof(double) * (size_t)ops);
	double t0 = now_us(), s0 = sys_us();
	int done = 0;
	for (; done < ops; done++) {
		off_t off = (off_t)(next_rand() % (uint64_t)((size - (off_t)block) / (off_t)block)) * (off_t)block;
		double a = now_us();
		if (pread(fd, buf, block, off) != (ssize_t)block) {
			printf("read at %lld: %s\n", (long long)off, strerror(errno));
			break;
		}
		lat[done] = now_us() - a;
	}
	double wall = now_us() - t0, sys = sys_us() - s0, sum = 0;
	for (int i = 0; i < done; i++)
		sum += lat[i];
	qsort(lat, (size_t)done, sizeof(double), cmp);
	printf("random %6zu B: %7.0f IOPS %7.1f MB/s  mean %7.1f us  p99 %8.1f us  system %5.1f%% of wall\n", block, done * 1e6 / wall,
	       done * (double)block / wall, sum / done, lat[done * 99 / 100], sys * 100 / wall);
	free(lat);
	free(buf);
}

static const char *largest_cache(void)
{
	static char best[1024];
	const char *dir = "/System/Library/Caches/com.apple.dyld";
	DIR *d = opendir(dir);
	off_t max = 0;
	if (!d)
		return NULL;
	for (struct dirent *e; (e = readdir(d));) {
		char p[1024];
		struct stat s;
		snprintf(p, sizeof(p), "%s/%s", dir, e->d_name);
		if (!stat(p, &s) && S_ISREG(s.st_mode) && s.st_size > max) {
			max = s.st_size;
			snprintf(best, sizeof(best), "%s", p);
		}
	}
	closedir(d);
	return max ? best : NULL;
}

int main(int argc, char **argv)
{
	const char *path = argc > 1 ? argv[1] : largest_cache();
	struct stat s;
	if (!path || stat(path, &s)) {
		printf("no file to read\n");
		return 1;
	}
	int fd = open(path, O_RDONLY);
	if (fd < 0 || fcntl(fd, F_NOCACHE, 1)) {
		printf("open %s: %s\n", path, strerror(errno));
		return 1;
	}
	printf("%s, %lld MiB\n", path, (long long)s.st_size >> 20);
	/* a fresh region each run, so that nothing is already resident */
	rng ^= (uint64_t)now_us();
	char *buf = aligned_alloc(16384, 1 << 20);
	off_t len = s.st_size > (off_t)(256 << 20) ? (off_t)(256 << 20) : s.st_size;
	off_t start = (off_t)(next_rand() % (uint64_t)((s.st_size - len) / (1 << 20) + 1)) << 20;
	double t0 = now_us(), s0 = sys_us();
	off_t got = 0;
	while (got < len) {
		ssize_t r = pread(fd, buf, 1 << 20, start + got);
		if (r <= 0)
			break;
		got += r;
	}
	double wall = now_us() - t0;
	printf("sequential 1 MiB: %7.1f MB/s  (%lld MiB)  system %5.1f%% of wall\n", got / wall, (long long)got >> 20, (sys_us() - s0) * 100 / wall);
	random_test(fd, s.st_size, 4096, 4000);
	random_test(fd, s.st_size, 16384, 2000);
	close(fd);
	return 0;
}
