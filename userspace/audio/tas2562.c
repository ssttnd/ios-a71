/*
 * TAS2562 (A71 speaker amp, I2C 0x4c) behind adspboot's cs35l41.h interface, so adspboot's --amp sequence drives it.
 * Built instead of cs35l41.c and geni_i2c.c (.github/workflows/audio.yml).
 *
 * Bus: QUP SE0 pins gpio49 (SDA) / gpio50 (SCL) on TLMM WEST, bit-banged open drain (tasprobe found the amp there,
 * wave35); reset gpio8 on TLMM NORTH is driven high. No speaker protection on this phone: hard limits here, whatever
 * the caller asks: only 0x4c is written, analog level at most AMP_LEVEL_MAX (8.5 dBV + 0.5 dB steps), digital
 * volume at most -20 dB, the amp leaves software shutdown only after every configured field reads back, and any
 * fault latch (over-temperature, over-current, clock error) shuts it down.
 *
 * Registers (book 0): page 0: 0x02 PWR_CTL (bits 1:0 mode 0 active, 1 mute, 2 shutdown; 2 VSNS_PD, 3 ISNS_PD),
 * 0x03 PB_CFG1 (bits 5:1 AMP_LEVEL), 0x06 TDM_CFG0 (rate 48 k, frame start), 0x07 TDM_CFG1 (RX offset 1 = I2S),
 * 0x08 TDM_CFG2 (5:4 RX_SCFG, 3:2 RX word length, 1:0 RX slot length), 0x24-0x27 interrupt latches, 0x7d REV_ID.
 * Page 2: 0x0c-0x0f DVC, 32-bit big endian, 0x40000000 = 0 dB.
 */
#include <errno.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include "pmem.h"
#include "cs35l41.h"
#include "geni_i2c.h"

#define TLMM_NORTH	0x03900000ull
#define TLMM_WEST	0x03500000ull
#define CTL(t, n)	((t) + 0x1000ull * (n))
#define IO(t, n)	((t) + 0x1000ull * (n) + 4)
#define SDA		49
#define SCL		50
#define RST		8
#define TAS_ADDR	0x4c
#define CFG_IN_PU	0x003u
#define CFG_OUT		0x200u

#define R_PAGE		0x00
#define R_PWR		0x02
#define R_PB1		0x03
#define R_TDM0		0x06
#define R_TDM1		0x07
#define R_TDM2		0x08
#define R_TDM3		0x09
#define R_LTCH0		0x24
#define R_REV		0x7d
#define R_BOOK		0x7f
#define PG2_DVC		0x0c

#define AMP_LEVEL_MAX	4	/* 8.5 .. 10.5 dBV */
#define VOL_MAX_DB	(-20)

static int dry, quiet;
static int cur_page = -1;
static uint32_t dvc_saved = 0x40000000u;
static uint8_t pb1_saved, pwr_saved, tdm2_saved;

void cs_set_dry(int d) { dry = d; }
void cs_set_quiet(int q) { quiet = q; }

static void spin_us(unsigned us)
{
	struct timespec a, b;
	clock_gettime(CLOCK_MONOTONIC, &a);
	do
		clock_gettime(CLOCK_MONOTONIC, &b);
	while ((b.tv_sec - a.tv_sec) * 1000000L + (b.tv_nsec - a.tv_nsec) / 1000 < (long)us);
}

static void line(unsigned pin, int release)
{
	wr32(CTL(TLMM_WEST, pin), release ? CFG_IN_PU : (CFG_IN_PU | CFG_OUT));
	spin_us(4);
}

static int level(unsigned pin) { return rd32(IO(TLMM_WEST, pin)) & 1; }

static int scl_high(void)
{
	line(SCL, 1);
	for (int i = 0; i < 2000; i++) {
		if (level(SCL))
			return 0;
		spin_us(5);
	}
	return -1;
}

static int bb_start(void)
{
	line(SDA, 1);
	if (scl_high())
		return -1;
	if (!level(SDA))
		return -2;
	line(SDA, 0);
	line(SCL, 0);
	return 0;
}

static void bb_stop(void)
{
	line(SDA, 0);
	scl_high();
	line(SDA, 1);
	spin_us(10);
}

static int bb_send(uint8_t b)
{
	for (int i = 7; i >= 0; i--) {
		line(SDA, (b >> i) & 1);
		if (scl_high())
			return -1;
		line(SCL, 0);
	}
	line(SDA, 1);
	if (scl_high())
		return -1;
	int nack = level(SDA);
	line(SCL, 0);
	return nack;
}

static uint8_t bb_recv(int ack)
{
	uint8_t b = 0;
	line(SDA, 1);
	for (int i = 7; i >= 0; i--) {
		scl_high();
		b |= (uint8_t)(level(SDA) << i);
		line(SCL, 0);
	}
	line(SDA, !ack);
	scl_high();
	line(SCL, 0);
	line(SDA, 1);
	return b;
}

static void bb_recover(void)
{
	line(SDA, 1);
	for (int i = 0; i < 9 && !level(SDA); i++) {
		line(SCL, 0);
		scl_high();
	}
	bb_stop();
}

int gi2c_init(void)
{
	static int done;
	if (done)
		return 0;
	if (dry)
		return done = 1, 0;
	/* reset high (out of hardware reset; it comes up in software shutdown) */
	wr32(IO(TLMM_NORTH, RST), 2);
	wr32(CTL(TLMM_NORTH, RST), CFG_OUT);
	usleep(2000);
	wr32(IO(TLMM_WEST, SDA), 0);
	wr32(IO(TLMM_WEST, SCL), 0);
	line(SDA, 1);
	line(SCL, 1);
	usleep(500);
	bb_recover();
	printf("amp: bit-banged I2C on gpio49/50, reset gpio8 high (cfg %#x io %#x)\n", rd32(CTL(TLMM_NORTH, RST)),
	       rd32(IO(TLMM_NORTH, RST)));
	done = 1;
	return 0;
}

static int raw_write(uint8_t reg, uint8_t val)
{
	if (dry)
		return 0;
	int r = bb_start();
	if (!r)
		r = bb_send(TAS_ADDR << 1);
	if (!r)
		r = bb_send(reg);
	if (!r)
		r = bb_send(val);
	bb_stop();
	return r;
}

static int raw_read(uint8_t reg, uint8_t *val)
{
	if (dry) {
		*val = 0;
		return 0;
	}
	int r = bb_start();
	if (!r)
		r = bb_send(TAS_ADDR << 1);
	if (!r)
		r = bb_send(reg);
	if (!r)
		r = bb_start();
	if (!r)
		r = bb_send((TAS_ADDR << 1) | 1);
	if (!r)
		*val = bb_recv(0);
	bb_stop();
	return r;
}

static int page(int p)
{
	if (cur_page == p)
		return 0;
	int r = raw_write(R_BOOK, 0) || raw_write(R_PAGE, (uint8_t)p);
	cur_page = r ? -1 : p;
	return r;
}

/* reg = page << 8 | register */
int cs_read(struct cs_amp *a, uint32_t reg, uint32_t *v)
{
	uint8_t b = 0;
	int r = page((int)(reg >> 8) & 0xff) || raw_read(reg & 0xff, &b);
	*v = b;
	if (!quiet)
		printf("amp: read  p%u 0x%02x = 0x%02x%s\n", (reg >> 8) & 0xff, reg & 0xff, b, r ? " (I2C error)" : "");
	return r ? EIO : 0;
}

int cs_write(struct cs_amp *a, uint32_t reg, uint32_t v)
{
	if (a->addr != TAS_ADDR) {
		printf("amp: refusing a write to %#x (only the TAS2562 at 0x4c)\n", a->addr);
		return EPERM;
	}
	int r = page((int)(reg >> 8) & 0xff) || raw_write(reg & 0xff, (uint8_t)v);
	if (!quiet)
		printf("amp: write p%u 0x%02x = 0x%02x%s\n", (reg >> 8) & 0xff, reg & 0xff, v & 0xff, r ? " (I2C error)" : "");
	return r ? EIO : 0;
}

static void expect(struct cs_amp *a, uint32_t reg, uint32_t mask, uint32_t val)
{
	if (a->nexp < CS_MAX_EXP)
		a->exp[a->nexp++] = (struct cs_exp){ reg, mask, val };
}

int cs_probe(struct cs_amp *a)
{
	if (a->addr != TAS_ADDR) {
		printf("amp: address %#x is not the A71's TAS2562 (0x4c): use --amp-addr 0x4c\n", a->addr);
		return EINVAL;
	}
	uint32_t rev = 0, pwr = 0, l0 = 0;
	cur_page = -1;
	if (cs_read(a, R_REV, &rev) || cs_read(a, R_PWR, &pwr) || cs_read(a, R_LTCH0, &l0))
		return EIO;
	printf("amp: TAS2562 rev %#x, PWR_CTL %#x (mode %u), latch0 %#x\n", rev, pwr, pwr & 3, l0);
	if ((pwr & 3) != 2) {
		printf("amp: not in software shutdown at probe: refusing\n");
		return EBUSY;
	}
	pwr_saved = (uint8_t)pwr;
	a->probed = 1;
	return 0;
}

static uint32_t dvc_coeff(int db)
{
	return (uint32_t)(1073741824.0 * pow(10.0, db / 20.0));
}

int cs_config(struct cs_amp *a, int vol_db, int boost)
{
	(void)boost;
	if (!a->probed)
		return EINVAL;
	if (vol_db > VOL_MAX_DB)
		vol_db = VOL_MAX_DB;
	int lvl = a->amp_gain < 0 ? 0 : a->amp_gain > AMP_LEVEL_MAX ? AMP_LEVEL_MAX : a->amp_gain;
	uint32_t pb1 = 0, tdm2 = 0, v = 0;
	if (cs_read(a, R_PB1, &pb1) || cs_read(a, R_TDM2, &tdm2))
		return EIO;
	pb1_saved = (uint8_t)pb1;
	tdm2_saved = (uint8_t)tdm2;
	/* DVC first: saved, then the limit */
	uint32_t d[4] = { 0 };
	for (int i = 0; i < 4; i++)
		if (cs_read(a, 0x200 | (PG2_DVC + i), &d[i]))
			return EIO;
	dvc_saved = d[0] << 24 | d[1] << 16 | d[2] << 8 | d[3];
	uint32_t c = dvc_coeff(vol_db);
	a->nexp = 0;
	for (int i = 0; i < 4; i++) {
		v = (c >> (24 - 8 * i)) & 0xff;
		if (cs_write(a, 0x200 | (PG2_DVC + i), v))
			return EIO;
		expect(a, 0x200 | (PG2_DVC + i), 0xff, v);
	}
	/* I2S: 48 kHz (TDM_CFG0 reset value 0x09), RX offset 1 (TDM_CFG1 0x02), 16-bit words in 16- or 32-bit slots,
	 * L+R downmix (the AFE sends stereo) */
	uint32_t t2 = (3u << 4) | (0u << 2) | (a->slot32 ? 2u : 0u);
	if (cs_write(a, R_TDM0, 0x09) || cs_write(a, R_TDM1, 0x02) || cs_write(a, R_TDM2, t2) || cs_write(a, R_TDM3, 0x10))
		return EIO;
	expect(a, R_TDM0, 0xff, 0x09);
	expect(a, R_TDM1, 0xff, 0x02);
	expect(a, R_TDM2, 0x3f, t2);
	uint32_t p = (pb1 & ~0x3eu) | ((uint32_t)lvl << 1);
	if (cs_write(a, R_PB1, p))
		return EIO;
	expect(a, R_PB1, 0x3e, (uint32_t)lvl << 1);
	expect(a, R_PWR, 0x03, 0x02);	/* still in software shutdown */
	printf("amp: configured: digital %d dB (DVC %#x, was %#x), analog level %d (%.1f dBV), I2S 48 k, %s slots, downmix\n",
	       vol_db, c, dvc_saved, lvl, 8.5 + 0.5 * lvl, a->slot32 ? "32-bit" : "16-bit");
	a->configured = 1;
	a->verified = 0;
	return 0;
}

int cs_verify(struct cs_amp *a)
{
	int bad = 0;
	for (int i = 0; i < a->nexp; i++) {
		uint32_t v = 0;
		if (cs_read(a, a->exp[i].reg, &v) || (v & a->exp[i].mask) != a->exp[i].val) {
			printf("amp: verify p%u 0x%02x: %#x, want %#x under %#x\n", (a->exp[i].reg >> 8) & 0xff,
			       a->exp[i].reg & 0xff, v, a->exp[i].val, a->exp[i].mask);
			bad = 1;
		}
	}
	a->verified = !bad;
	printf("amp: verify %s (%d fields)\n", bad ? "FAILED" : "ok", a->nexp);
	return bad ? EIO : 0;
}

int cs_check(struct cs_amp *a)
{
	uint32_t l[4] = { 0 };
	for (int i = 0; i < 4; i++)
		cs_read(a, R_LTCH0 + i, &l[i]);
	a->err_bits = l[0] | l[1] << 8 | l[2] << 16 | l[3] << 24;
	/* latch0: bit 0 over-temperature, 1 over-current, 2 TDM clock error */
	if (l[0] & 0x07) {
		printf("amp: FAULT latches %02x %02x %02x %02x\n", l[0], l[1], l[2], l[3]);
		return 1;
	}
	return 0;
}

int cs_power_up(struct cs_amp *a)
{
	if (!a->configured || !a->verified) {
		printf("amp: power up refused: not configured and verified\n");
		return EINVAL;
	}
	/* active, both sense blocks off */
	if (cs_write(a, R_PWR, 0x0c))
		return EIO;
	usleep(10000);
	uint32_t p = 0;
	cs_read(a, R_PWR, &p);
	int fault = cs_check(a);
	printf("amp: power up: PWR_CTL %#x (mode %u)%s\n", p, p & 3, fault ? ", fault" : "");
	if ((p & 3) != 0 || fault) {
		cs_power_down(a);
		return EIO;
	}
	a->up = 1;
	return 0;
}

void cs_out_mute(struct cs_amp *a, const char *tag)
{
	uint32_t p = 0;
	cs_read(a, R_PWR, &p);
	printf("amp: %s: PWR_CTL %#x (mode %u)\n", tag, p, p & 3);
}

void cs_diag(struct cs_amp *a, const char *tag)
{
	int q = quiet;
	quiet = 1;
	uint32_t p = 0, pb = 0, t[4] = { 0 }, l[4] = { 0 };
	cs_read(a, R_PWR, &p);
	cs_read(a, R_PB1, &pb);
	for (int i = 0; i < 4; i++) {
		cs_read(a, R_TDM0 + i, &t[i]);
		cs_read(a, R_LTCH0 + i, &l[i]);
	}
	quiet = q;
	printf("amp: diag %s: PWR %02x PB1 %02x TDM %02x %02x %02x %02x latch %02x %02x %02x %02x\n", tag, p, pb, t[0], t[1],
	       t[2], t[3], l[0], l[1], l[2], l[3]);
}

int cs_standby(struct cs_amp *a)
{
	int r = cs_write(a, R_PWR, 0x0e);
	a->up = 0;
	return r;
}

void cs_power_down(struct cs_amp *a)
{
	cs_write(a, R_PWR, 0x0e);	/* software shutdown, sense off */
	usleep(5000);
	if (a->configured) {
		for (int i = 0; i < 4; i++)
			cs_write(a, 0x200 | (PG2_DVC + i), (dvc_saved >> (24 - 8 * i)) & 0xff);
		cs_write(a, R_PB1, pb1_saved);
		cs_write(a, R_TDM2, tdm2_saved);
	}
	uint32_t p = 0;
	cs_read(a, R_PWR, &p);
	printf("amp: power down: PWR_CTL %#x (mode %u)\n", p, p & 3);
	a->up = 0;
	a->configured = 0;
	a->verified = 0;
}

int cs_resume_check(struct cs_amp *a)
{
	uint32_t pb = 0;
	return cs_read(a, R_PB1, &pb) || (pb & 0x3e) > (AMP_LEVEL_MAX << 1);
}
