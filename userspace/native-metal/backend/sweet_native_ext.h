/* SPDX-License-Identifier: MIT */
#ifndef SWEET_NATIVE_METAL_EXT_H
#define SWEET_NATIVE_METAL_EXT_H
#include "air_metal.h"
#include <stdbool.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
#define SWEET_NATIVE_METAL_ABI 0x53574e01u
unsigned rwa_native_abi(void);
struct rwa_library;
struct rwa_library *rwa_library_open(const void *, size_t, char *, size_t);
void rwa_library_close(struct rwa_library *);
unsigned rwa_library_count(const struct rwa_library *);
const char *rwa_library_name(const struct rwa_library *, unsigned);
int rwa_library_function(const struct rwa_library *, unsigned, const void **, size_t *, unsigned *);
/* Constant bytes are packed little endian. The AIR frontend supports up to
 * 64 bits, including small vectors such as QuartzCore's uchar3 opcodes. */
struct rwa_constant { unsigned index; const char *name; uint64_t bits; unsigned bytes; };
int rwa_set_constants(struct rwa_device *, unsigned stage, const struct rwa_constant *, unsigned, char *, size_t);
/* One context transaction per committed Metal command buffer. end waits once,
 * including the prefix of a command buffer whose later encoding was refused. */
int rwa_barrier(struct rwa_device *, char *, size_t);
int sweet_transport_command_begin(void);
void sweet_transport_command_end(void);
uint64_t sweet_transport_submitted_fence(void);
int sweet_transport_poll_fence(uint64_t);
int rwa_command_begin(struct rwa_device *, char *, size_t);
int rwa_command_end(struct rwa_device *, char *, size_t);
/* Asynchronous end: hands the transaction to the GPU without waiting and
 * returns the kernel fence that retires it. rwa_command_poll answers 0 when
 * that fence has retired (GPU-written imports returned to the CPU), 1 while
 * pending, -1 on failure or when give_up is set and it is still pending. */
int rwa_command_submit(struct rwa_device *, uint64_t *, char *, size_t);
int rwa_command_poll(struct rwa_device *, uint64_t, bool, char *, size_t);
int rwa_clear(struct rwa_device *, struct rwa_texture *const *, unsigned, unsigned,
              const float [8][4], struct rwa_texture *, const float *, const unsigned *, char *, size_t);
#ifdef __cplusplus
}
#endif
#endif
