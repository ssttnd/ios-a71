/*
 * tasprobe: read-only probe of the A71's speaker amp, TI TAS2562 at I2C address 0x4c (live DT:
 * /soc/i2c@0x880000/tas2562@4c, reset gpio 8, irq gpio 89, port 0x1002).
 *
 * The bus is QUP SE0 (gpio49/50, function qup00, TLMM WEST). Nothing on iOS sets up SE0's clocks or its
 * GENI firmware, so this does not use the controller: it switches both pins to GPIO and drives them
 * open-drain (output low or released with the pull-up), as the kernel's touch bit-bang (0185) does.
 * The reset line (gpio8, TLMM NORTH) is driven high so the amp leaves hardware reset; a TAS2562 comes out
 * of reset in software shutdown (PWR_CTL mode 2), so nothing is powered towards the speaker.
 *
 * Only register reads, plus page/book selects (0x00/0x7f = 0). At the end every pin gets its old
 * configuration back, the reset line included. Needs /dev/pmem (sweetpmem=1). Output on stdout.
 *
 *   tasprobe            probe, dump page 0 of book 0, restore
 *   tasprobe --keep     same, but leave reset released (for a following config step)
 */
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define TLMM_NORTH	0x03900000ull
#define TLMM_WEST	0x03500000ull
#define CTL(t, n)	((t) + 0x1000ull * (n))
#define IO(t, n)	((t) + 0x1000ull * (n) + 4)
#define PIN_RST		8	/* NORTH */
#define PIN_A		49	/* WEST, qup00 lane 0 */
#define PIN_B		50	/* WEST, qup00 lane 1 */
#define AMP		0x4c

static int pm = -1;
static unsigned sda = PIN_A, scl = PIN_B;

static uint32_t rd(uint64_t pa)
{
	uint32_t v = 0xffffffffu;
	if (pread(pm, &v, 4, (off_t)pa) != 4)
		fprintf(stderr, "tasprobe: read %llx: %s\n", (unsigned long long)pa, strerror(errno));
	return v;
}

static void wr(uint64_t pa, uint32_t v)
{
	if (pwrite(pm, &v, 4, (off_t)pa) != 4)
		fprintf(stderr, "tasprobe: write %llx: %s\n", (unsigned long long)pa, strerror(errno));
}

static void spin_us(unsigned us)
{
	struct timespec a, b;
	clock_gettime(CLOCK_MONOTONIC, &a);
	do
		clock_gettime(CLOCK_MONOTONIC, &b);
	while ((b.tv_sec - a.tv_sec) * 1000000L + (b.tv_nsec - a.tv_nsec) / 1000 < (long)us);
}

/* cfg: [1:0] pull (3 up), [5:2] function, [8:6] drive (2 mA = 0), bit 9 output enable. io: bit 0 in, bit 1 out. */
#define CFG_IN_PU	0x003u
#define CFG_OUT		0x200u

/* open drain: low = output enabled with value 0; released = input with pull-up */
static void line(unsigned pin, int release)
{
	wr(CTL(TLMM_WEST, pin), release ? CFG_IN_PU : (CFG_IN_PU | CFG_OUT));
	spin_us(4);
}

static int level(unsigned pin)
{
	return rd(IO(TLMM_WEST, pin)) & 1;
}

static int scl_high(void)
{
	line(scl, 1);
	for (int i = 0; i < 2000; i++) {
		if (level(scl))
			return 0;
		spin_us(5);
	}
	return -1;	/* held low */
}

static int start(void)
{
	line(sda, 1);
	if (scl_high())
		return -1;
	if (!level(sda))
		return -2;
	line(sda, 0);
	line(scl, 0);
	return 0;
}

static void stop(void)
{
	line(sda, 0);
	scl_high();
	line(sda, 1);
	spin_us(10);
}

static int send(uint8_t b)	/* 0 = ACK */
{
	for (int i = 7; i >= 0; i--) {
		line(sda, (b >> i) & 1);
		if (scl_high())
			return -1;
		line(scl, 0);
	}
	line(sda, 1);
	if (scl_high())
		return -1;
	int nack = level(sda);
	line(scl, 0);
	return nack;
}

static uint8_t recv(int ack)
{
	uint8_t b = 0;
	line(sda, 1);
	for (int i = 7; i >= 0; i--) {
		scl_high();
		b |= (uint8_t)(level(sda) << i);
		line(scl, 0);
	}
	line(sda, !ack);
	scl_high();
	line(scl, 0);
	line(sda, 1);
	return b;
}

static void recover(void)	/* 9 clocks and a stop, for a slave left mid-byte */
{
	line(sda, 1);
	for (int i = 0; i < 9 && !level(sda); i++) {
		line(scl, 0);
		scl_high();
	}
	stop();
}

static int reg_write(uint8_t reg, uint8_t val)
{
	int r = start();
	if (!r)
		r = send(AMP << 1) ?: send(reg) ?: send(val);
	stop();
	return r;
}

static int reg_read(uint8_t reg, uint8_t *val)
{
	int r = start();
	if (!r)
		r = send(AMP << 1) ?: send(reg);
	if (!r)
		r = start() ?: send((AMP << 1) | 1);
	if (!r)
		*val = recv(0);
	stop();
	return r;
}

static int addr_ack(void)
{
	int r = start();
	if (!r)
		r = send(AMP << 1);
	stop();
	return r;
}

int main(int argc, char **argv)
{
	int keep = argc > 1 && !strcmp(argv[1], "--keep");
	pm = open("/dev/pmem", O_RDWR);
	if (pm < 0) {
		printf("tasprobe: /dev/pmem: %s (sweetpmem=1?)\n", strerror(errno));
		return 1;
	}
	uint32_t rst_cfg = rd(CTL(TLMM_NORTH, PIN_RST)), rst_io = rd(IO(TLMM_NORTH, PIN_RST));
	uint32_t a_cfg = rd(CTL(TLMM_WEST, PIN_A)), b_cfg = rd(CTL(TLMM_WEST, PIN_B));
	uint32_t a_io = rd(IO(TLMM_WEST, PIN_A)), b_io = rd(IO(TLMM_WEST, PIN_B));
	printf("tasprobe: before: reset gpio8 cfg %#x io %#x; gpio49 cfg %#x io %#x; gpio50 cfg %#x io %#x\n",
	       rst_cfg, rst_io, a_cfg, a_io, b_cfg, b_io);

	/* reset: output high (value first, then function GPIO + output enable) */
	wr(IO(TLMM_NORTH, PIN_RST), 2);
	wr(CTL(TLMM_NORTH, PIN_RST), CFG_OUT);
	usleep(5000);
	printf("tasprobe: reset released: gpio8 cfg %#x io %#x\n", rd(CTL(TLMM_NORTH, PIN_RST)), rd(IO(TLMM_NORTH, PIN_RST)));

	/* both bus pins: value 0 latched, released (input + pull-up) */
	wr(IO(TLMM_WEST, PIN_A), 0);
	wr(IO(TLMM_WEST, PIN_B), 0);
	line(PIN_A, 1);
	line(PIN_B, 1);
	usleep(1000);
	printf("tasprobe: idle levels gpio49 %d gpio50 %d (1 = pulled up)\n", level(PIN_A), level(PIN_B));

	/* which lane is SDA: try 49 = SDA first (Qualcomm I2C lane 0), then the other way round */
	int found = 0;
	for (int k = 0; k < 2 && !found; k++) {
		sda = k ? PIN_B : PIN_A;
		scl = k ? PIN_A : PIN_B;
		recover();
		int r = addr_ack();
		printf("tasprobe: sda gpio%u scl gpio%u: address %#x -> %s (%d)\n", sda, scl, AMP,
		       r == 0 ? "ACK" : r == 1 ? "NACK" : "bus error", r);
		found = r == 0;
	}
	if (found) {
		int r = reg_write(0x00, 0x00) ?: reg_write(0x7f, 0x00);
		printf("tasprobe: page 0 / book 0 select -> %d\n", r);
		static const uint8_t regs[] = { 0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0a, 0x0b, 0x0c,
						0x0d, 0x0e, 0x0f, 0x10, 0x11, 0x12, 0x13, 0x1a, 0x1b, 0x1c, 0x1d, 0x24, 0x25,
						0x26, 0x27, 0x28, 0x29, 0x2a, 0x2b, 0x31, 0x32, 0x33, 0x34, 0x35, 0x36, 0x7d,
						0x7e, 0x7f };
		char lineb[1024];
		int n = 0;
		for (unsigned i = 0; i < sizeof(regs); i++) {
			uint8_t v = 0;
			int e = reg_read(regs[i], &v);
			n += snprintf(lineb + n, sizeof(lineb) - n, " %02x=%s%02x", regs[i], e ? "!" : "", v);
			if (n > 900)
				break;
		}
		printf("tasprobe: page 0:%s\n", lineb);
		printf("tasprobe: PWR_CTL (0x02) mode = bits 1:0 (0 active, 1 mute, 2 shutdown); REV_ID at 0x7d\n");
	}

	/* restore the bus pins, then the reset line unless --keep */
	wr(CTL(TLMM_WEST, PIN_A), a_cfg);
	wr(IO(TLMM_WEST, PIN_A), a_io & 2);
	wr(CTL(TLMM_WEST, PIN_B), b_cfg);
	wr(IO(TLMM_WEST, PIN_B), b_io & 2);
	if (!keep) {
		wr(IO(TLMM_NORTH, PIN_RST), rst_io & 2);
		wr(CTL(TLMM_NORTH, PIN_RST), rst_cfg);
	}
	printf("tasprobe: restored: gpio8 cfg %#x io %#x%s; gpio49 cfg %#x; gpio50 cfg %#x\n", rd(CTL(TLMM_NORTH, PIN_RST)),
	       rd(IO(TLMM_NORTH, PIN_RST)), keep ? " (kept released)" : "", rd(CTL(TLMM_WEST, PIN_A)), rd(CTL(TLMM_WEST, PIN_B)));
	close(pm);
	return found ? 0 : 2;
}
