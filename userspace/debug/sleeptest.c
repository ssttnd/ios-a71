/*
 * How long usleep really takes on the phone, at normal and at real-time priority.
 * The audio daemon sleeps 2 ms per loop, yet its FIFO data and WRITE_DONE events arrive in lumps of about 40 ms.
 */
#include <mach/mach.h>
#include <mach/mach_time.h>
#include <mach/thread_policy.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

static int cmp(const void *a, const void *b)
{
	uint32_t x = *(const uint32_t *)a, y = *(const uint32_t *)b;
	return x < y ? -1 : x > y;
}

static void measure(unsigned us, unsigned n, const char *what)
{
	static uint32_t got[2000];
	mach_timebase_info_data_t tb;
	mach_timebase_info(&tb);
	n = n > 2000 ? 2000 : n;
	for (unsigned i = 0; i < n; i++) {
		uint64_t a = mach_absolute_time();
		usleep(us);
		uint64_t b = mach_absolute_time();
		got[i] = (uint32_t)((b - a) * tb.numer / tb.denom / 1000);
	}
	qsort(got, n, sizeof(got[0]), cmp);
	printf("%s usleep(%u): min %u p50 %u p90 %u p99 %u max %u us\n", what, us, got[0], got[n / 2], got[n * 9 / 10], got[n * 99 / 100], got[n - 1]);
	fflush(stdout);
}

static void set_realtime(void)
{
	mach_timebase_info_data_t tb;
	mach_timebase_info(&tb);
	const uint64_t ms = 1000000ull * tb.denom / tb.numer;
	thread_time_constraint_policy_data_t p = { (uint32_t)(10 * ms), (uint32_t)(2 * ms), (uint32_t)(4 * ms), 0 };
	p.preemptible = 1;
	kern_return_t k = thread_policy_set(mach_thread_self(), THREAD_TIME_CONSTRAINT_POLICY, (thread_policy_t)&p,
	    THREAD_TIME_CONSTRAINT_POLICY_COUNT);
	printf("real-time policy: %s\n", k == KERN_SUCCESS ? "set" : mach_error_string(k));
}

int main(void)
{
	static const unsigned d[] = { 1000, 2000, 10000 };
	for (unsigned i = 0; i < 3; i++)
		measure(d[i], d[i] == 10000 ? 150 : 400, "normal");
	set_realtime();
	for (unsigned i = 0; i < 3; i++)
		measure(d[i], d[i] == 10000 ? 150 : 400, "rt    ");
	return 0;
}
