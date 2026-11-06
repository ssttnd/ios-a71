/*
 * air_specialization_check: the generic QuartzCore functions, specialized
 * with the function constants CoreAnimation itself uses, against Apple's own
 * specialization of the same pipelines.
 *
 *   air_specialization_check default.metallib pipelines.desc functions [-v]
 *
 * QuartzCore.framework/default.pipelinelib carries, for 451 pipelines, the
 * function constants each was built with (pipelines.desc) and the AIR Apple's
 * compiler made by specializing default.metallib with them (functions: one
 * MTLB container per pipeline, in the same order). For each pipeline this
 * translates
 *
 *   A: the GENERIC function of default.metallib, with those constants, and
 *   B: Apple's specialized module, which has no constants left,
 *
 * runs NIR's own folding over both, and compares what every texture
 * instruction reads: its op, its texture's Metal index and its sampler (an
 * API [[sampler(n)]] or an inline constexpr sampler's state word). A pointer
 * resolved wrongly at translation -- the wrong sampler cell, the wrong side
 * of a function-constant choice -- shows up as a different set.
 *
 * Host-only (Linux, Mesa build tree); it executes no GPU instructions.
 */

#include <fcntl.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

#include "nir.h"
#include "nir_builder.h"
#include "nir_air.h"
#include "util/ralloc.h"

static const nir_shader_compiler_options options = { .lower_fdiv = false };

static const uint8_t *
map_file(const char *path, size_t *size)
{
   int fd = open(path, O_RDONLY);
   struct stat st;
   if (fd < 0 || fstat(fd, &st)) {
      perror(path);
      exit(2);
   }
   *size = st.st_size;
   void *p = mmap(NULL, st.st_size, PROT_READ, MAP_PRIVATE, fd, 0);
   close(fd);
   if (p == MAP_FAILED) {
      perror(path);
      exit(2);
   }
   return p;
}

/* -- pipelines.desc ---------------------------------------------------- */

struct desc_constant {
   const char *name;
   unsigned type;      /* MTLDataType: 49 uchar, 51 uchar3, 53 bool */
   uint64_t bits;      /* little-endian packed, component i at byte i */
   unsigned len;
};

struct desc_entry {
   const char *function;
   unsigned num;
   struct desc_constant c[96];
};

/* A constant record ends in its NUL-terminated name, preceded by
 *   02 04 <MTLDataType u32 LE> 03 <n> <n value bytes>
 * A pipeline's function is named after `0c 00 00 00 01 00 00 00`.
 */
static unsigned
parse_desc(void *ctx, const uint8_t *d, size_t size, const struct air_metallib *generic,
           struct desc_entry **out)
{
   unsigned cap = 512, n = 0;
   struct desc_entry *e = rzalloc_array(ctx, struct desc_entry, cap);
   for (size_t p = 8; p < size; p++) {
      /* a string starts at p: find its end */
      size_t q = p;
      while (q < size && d[q] >= 0x20 && d[q] < 0x7f)
         q++;
      if (q == p || q >= size || d[q] != 0)
         continue;
      const char *s = (const char *)d + p;
      size_t len = q - p;

      static const uint8_t fn_tag[8] = { 0x0c, 0, 0, 0, 1, 0, 0, 0 };
      if (p >= 8 && !memcmp(d + p - 8, fn_tag, 8)) {
         /* Pipelines also name unspecialized functions (std_vert0_lph, ...);
          * only one that declares constants has a module in `functions`.
          */
         for (unsigned i = 0; i < generic->num_functions; i++) {
            const struct air_metallib_function *gf = &generic->functions[i];
            struct air_constant_value *decl;
            if (strlen(gf->name) != len || memcmp(gf->name, s, len) ||
                !air_module_function_constants(ctx, gf->bitcode, gf->bitcode_size, &decl))
               continue;
            if (n == cap)
               cap *= 2, e = rerzalloc(ctx, e, struct desc_entry, n, cap);
            e[n++].function = ralloc_strndup(ctx, s, len);
            break;
         }
         continue;
      }
      if (!n)
         continue;
      for (unsigned vlen = 1; vlen <= 8; vlen++) {
         if (p < vlen + 8)
            break;
         size_t v = p - vlen;
         if (d[v - 1] != vlen || d[v - 2] != 0x03 || d[v - 8] != 0x02 || d[v - 7] != 0x04 ||
             d[v - 5] || d[v - 4] || d[v - 3])
            continue;
         struct desc_entry *cur = &e[n - 1];
         if (cur->num == ARRAY_SIZE(cur->c))
            break;
         struct desc_constant *k = &cur->c[cur->num++];
         k->name = ralloc_strndup(ctx, s, len);
         k->type = d[v - 6];
         k->len = vlen;
         k->bits = 0;
         for (unsigned b = 0; b < vlen; b++)
            k->bits |= (uint64_t)d[v + b] << (8 * b);
         break;
      }
   }
   *out = e;
   return n;
}

/* -- what the texture instructions read -------------------------------- */

static const char *
resource_name(void *ctx, const struct air_shader_info *info, enum air_resource_kind kind,
              unsigned index)
{
   for (unsigned i = 0; i < info->num_resources; i++) {
      const struct air_resource *r = &info->resources[i];
      if (r->kind != kind || r->index != index)
         continue;
      if (r->inline_sampler)
         return ralloc_asprintf(ctx, "inline:%016" PRIx64, r->sampler_state);
      return ralloc_asprintf(ctx, "%s%u", kind == AIR_RESOURCE_SAMPLER ? "s" : "t", index);
   }
   return ralloc_asprintf(ctx, "?%u", index);
}

static const char *
texop_name(nir_texop op)
{
   switch (op) {
   case nir_texop_tex: return "tex";
   case nir_texop_txb: return "txb";
   case nir_texop_txl: return "txl";
   case nir_texop_txd: return "txd";
   case nir_texop_txf: return "txf";
   case nir_texop_txf_ms: return "txf_ms";
   case nir_texop_txs: return "txs";
   case nir_texop_lod: return "lod";
   case nir_texop_tg4: return "tg4";
   case nir_texop_query_levels: return "query_levels";
   default: return "other";
   }
}

#include "air_live_resources.inc"

static int
compare_lines(const void *a, const void *b)
{
   return strcmp(*(const char *const *)a, *(const char *const *)b);
}

/* sorted, de-duplicated "op texture sampler" lines */
static char *
tex_signature(void *ctx, nir_shader *s, const struct air_shader_info *info, unsigned *count)
{
   char **lines = NULL;
   unsigned n = 0;
   nir_foreach_function_impl(impl, s) {
      nir_foreach_block(block, impl) {
         nir_foreach_instr(instr, block) {
            if (instr->type != nir_instr_type_tex)
               continue;
            nir_tex_instr *tex = nir_instr_as_tex(instr);
            bool dynamic = nir_tex_instr_src_index(tex, nir_tex_src_texture_offset) >= 0 ||
                           nir_tex_instr_src_index(tex, nir_tex_src_sampler_offset) >= 0 ||
                           nir_tex_instr_src_index(tex, nir_tex_src_texture_handle) >= 0;
            bool sampled = tex->op == nir_texop_tex || tex->op == nir_texop_txb ||
                           tex->op == nir_texop_txl || tex->op == nir_texop_txd ||
                           tex->op == nir_texop_tg4;
            lines = reralloc(ctx, lines, char *, n + 1);
            lines[n++] = ralloc_asprintf(
               ctx, "%s %s %s%s", texop_name(tex->op),
               resource_name(ctx, info, AIR_RESOURCE_TEXTURE, tex->texture_index),
               sampled ? resource_name(ctx, info, AIR_RESOURCE_SAMPLER, tex->sampler_index)
                       : "-",
               dynamic ? " dynamic" : "");
         }
      }
   }
   struct native_resource_use buffers = native_resource_usage(s);
   for (unsigned i = 0; i < 64; i++) {
      if (buffers.ubo & (UINT64_C(1) << i)) {
         lines = reralloc(ctx, lines, char *, n + 1);
         lines[n++] = ralloc_asprintf(ctx, "ubo b%u", i);
      }
      if (buffers.ssbo & (UINT64_C(1) << i)) {
         lines = reralloc(ctx, lines, char *, n + 1);
         lines[n++] = ralloc_asprintf(ctx, "ssbo b%u", i);
      }
   }
   if (buffers.any_ubo || buffers.any_ssbo) {
      lines = reralloc(ctx, lines, char *, n + 1);
      lines[n++] = ralloc_asprintf(ctx, "dynamic ubo=%u ssbo=%u", buffers.any_ubo, buffers.any_ssbo);
   }
   *count = n;
   qsort(lines, n, sizeof(*lines), compare_lines);
   char *sig = ralloc_strdup(ctx, "");
   for (unsigned i = 0; i < n; i++)
      if (!i || strcmp(lines[i], lines[i - 1]))
         ralloc_asprintf_append(&sig, "%s\n", lines[i]);
   return sig;
}

static unsigned
count_instrs(nir_shader *s)
{
   unsigned n = 0;
   nir_foreach_function_impl(impl, s)
      nir_foreach_block(block, impl)
         nir_foreach_instr(instr, block)
            n += instr->type != nir_instr_type_load_const && instr->type != nir_instr_type_undef;
   return n;
}

static nir_shader *
translate(void *ctx, const void *bc, size_t size, const struct air_constant_value *fc,
          unsigned num_fc, struct air_shader_info *info, const char **err)
{
   struct air_to_nir_options o = { .constants = fc, .num_constants = num_fc };
   nir_shader *s = air_to_nir(ctx, bc, size, NULL, &o, &options, info, err);
   if (s) {
      nir_validate_shader(s, "after air_to_nir");
      native_prune_direct_resources(s, info);
   }
   return s;
}

static bool
check_buffer_liveness(void)
{
   struct air_resource resources[3] = {
      {.kind=AIR_RESOURCE_CONSTANT_BUFFER,.index=0,.argbuf_set=~0u},
      {.kind=AIR_RESOURCE_CONSTANT_BUFFER,.index=1,.argbuf_set=~0u},
      {.kind=AIR_RESOURCE_DEVICE_BUFFER,.index=2,.argbuf_set=~0u},
   };
   for (unsigned dynamic = 0; dynamic < 2; dynamic++) {
      nir_builder b = nir_builder_init_simple_shader(MESA_SHADER_COMPUTE, &options, "buffer liveness");
      nir_def *zero = nir_imm_int(&b, 0);
      /* This discarded read must not impose an API binding. */
      nir_load_ubo(&b, 1, 32, zero, zero, .align_mul=4, .range=4);
      nir_def *index = dynamic ? nir_load_local_invocation_index(&b) : nir_imm_int(&b, 1);
      nir_def *live = nir_load_ubo(&b, 1, 32, index, zero, .align_mul=4, .range=4);
      nir_store_ssbo(&b, live, nir_imm_int(&b, 2), zero, .align_mul=4, .write_mask=1);
      struct air_resource copy[3];memcpy(copy,resources,sizeof copy);
      struct air_shader_info info = {.resources=copy,.num_resources=3};
      unsigned removed = native_prune_direct_resources(b.shader, &info);
      struct native_resource_use use = native_resource_usage(b.shader);
      bool ok = dynamic ? removed==0 && use.any_ubo
                        : removed==1 && info.num_resources==2 && info.resources[0].index==1;
      ok &= (use.ssbo & (UINT64_C(1)<<2)) != 0;
      /* Argument-buffer members must retain their shared encoder layout. */
      struct air_resource member=resources[0];member.argbuf_set=0;
      ok &= native_resource_active(use,&member);
      ralloc_free(b.shader);
      if (!ok) {fprintf(stderr,"buffer liveness regression failed (dynamic=%u)\n",dynamic);return false;}
   }
   return true;
}

static bool
check_texture_liveness(void)
{
   struct air_resource resources[5] = {
      {.kind=AIR_RESOURCE_TEXTURE,.index=0,.argbuf_set=~0u},
      {.kind=AIR_RESOURCE_TEXTURE,.index=5,.argbuf_set=~0u},
      {.kind=AIR_RESOURCE_SAMPLER,.index=1,.argbuf_set=~0u},
      {.kind=AIR_RESOURCE_SAMPLER,.index=3,.argbuf_set=~0u},
      {.kind=AIR_RESOURCE_DEVICE_BUFFER,.index=2,.argbuf_set=~0u},
   };
   for (unsigned dynamic=0;dynamic<2;dynamic++) {
      nir_builder b=nir_builder_init_simple_shader(MESA_SHADER_COMPUTE,&options,"texture liveness");
      nir_tex_instr *t=nir_tex_instr_create(b.shader,dynamic?3:1);
      t->op=nir_texop_tex;t->sampler_dim=GLSL_SAMPLER_DIM_2D;
      t->dest_type=nir_type_float32;t->coord_components=2;
      t->texture_index=0;t->sampler_index=1;
      t->src[0].src_type=nir_tex_src_coord;
      t->src[0].src=nir_src_for_ssa(nir_imm_vec2(&b,0.5,0.5));
      if (dynamic) {
         nir_def *offset=nir_load_local_invocation_index(&b);
         t->src[1].src_type=nir_tex_src_texture_offset;t->src[1].src=nir_src_for_ssa(offset);
         t->src[2].src_type=nir_tex_src_sampler_offset;t->src[2].src=nir_src_for_ssa(offset);
      }
      nir_def_init(&t->instr,&t->def,4,32);nir_builder_instr_insert(&b,&t->instr);
      nir_store_ssbo(&b,&t->def,nir_imm_int(&b,2),nir_imm_int(&b,0),.align_mul=4,.write_mask=15);
      struct air_resource copy[5];memcpy(copy,resources,sizeof copy);
      struct air_shader_info info={.resources=copy,.num_resources=5};
      unsigned removed=native_prune_direct_resources(b.shader,&info);
      bool ok=dynamic ? removed==0 : removed==2 && info.num_resources==3;
      ralloc_free(b.shader);
      if (!ok) {fprintf(stderr,"texture liveness regression failed (dynamic=%u)\n",dynamic);return false;}
   }
   return true;
}

int
main(int argc, char **argv)
{
   if (argc < 4) {
      fprintf(stderr, "usage: %s default.metallib pipelines.desc functions [-v] [-e N]\n",
              argv[0]);
      return 2;
   }
   bool verbose = false;
   int only = -1;   /* -e N: that entry alone, its constants listed */
   for (int a = 4; a < argc; a++) {
      if (!strcmp(argv[a], "-v"))
         verbose = true;
      else if (!strcmp(argv[a], "-e") && a + 1 < argc)
         only = atoi(argv[++a]);
   }
   glsl_type_singleton_init_or_ref();
   if (!check_buffer_liveness() || !check_texture_liveness()) return 1;
   void *ctx = ralloc_context(NULL);
   const char *err;

   size_t gsize, dsize, fsize;
   const uint8_t *g = map_file(argv[1], &gsize);
   const uint8_t *d = map_file(argv[2], &dsize);
   const uint8_t *f = map_file(argv[3], &fsize);

   struct air_metallib generic;
   if (!air_metallib_parse(ctx, g, gsize, &generic, &err)) {
      fprintf(stderr, "%s: %s\n", argv[1], err);
      return 2;
   }
   struct desc_entry *entries;
   unsigned num_entries = parse_desc(ctx, d, dsize, &generic, &entries);

   /* the specialized modules, in order */
   unsigned num_spec = 0, mismatched = 0, failed = 0, failed_spec = 0, matched = 0;
   const uint8_t *p = f;
   for (unsigned i = 0; i < num_entries; i++) {
      p = memmem(p, fsize - (p - f), "MTLB", 4);
      if (!p)
         break;
      uint64_t size;
      memcpy(&size, p + 0x10, 8);
      struct air_metallib spec;
      if (!size || size > fsize - (p - f) ||
          !air_metallib_parse(ctx, p, size, &spec, &err) || spec.num_functions != 1) {
         fprintf(stderr, "functions: container %u unreadable\n", i);
         return 2;
      }
      p += size;
      num_spec++;
      struct desc_entry *e = &entries[i];
      if (strcmp(spec.functions[0].name, e->function)) {
         fprintf(stderr, "entry %u: desc names %s, functions holds %s\n", i, e->function,
                 spec.functions[0].name);
         return 2;
      }

      const struct air_metallib_function *gf = NULL;
      for (unsigned k = 0; k < generic.num_functions; k++)
         if (!strcmp(generic.functions[k].name, e->function))
            gf = &generic.functions[k];

      /* the generic function's constants, given the values the desc names */
      struct air_constant_value *decl;
      unsigned num_decl = air_module_function_constants(ctx, gf->bitcode, gf->bitcode_size,
                                                        &decl);
      struct air_constant_value *fc = rzalloc_array(ctx, struct air_constant_value,
                                                    MAX2(num_decl, 1));
      unsigned num_fc = 0, unknown = 0;
      for (unsigned k = 0; k < e->num; k++) {
         bool found = false;
         for (unsigned j = 0; j < num_decl; j++) {
            if (decl[j].name && !strcmp(decl[j].name, e->c[k].name)) {
               fc[num_fc] = decl[j];
               fc[num_fc++].bits = e->c[k].bits;
               found = true;
            }
         }
         unknown += !found;
      }

      if (only >= 0 && (int)i != only)
         continue;
      if (only >= 0)
         for (unsigned k = 0; k < e->num; k++)
            printf("  %s type %u = 0x%" PRIx64 "\n", e->c[k].name, e->c[k].type, e->c[k].bits);
      void *mctx = ralloc_context(ctx);
      struct air_shader_info ia, ib;
      const char *ea = NULL, *eb = NULL;
      nir_shader *a = translate(mctx, gf->bitcode, gf->bitcode_size, fc, num_fc, &ia, &ea);
      nir_shader *b = translate(mctx, spec.functions[0].bitcode,
                                spec.functions[0].bitcode_size, NULL, 0, &ib, &eb);
      unsigned na = 0, nb = 0;
      const char *sa = a ? tex_signature(mctx, a, &ia, &na) : NULL;
      const char *sb = b ? tex_signature(mctx, b, &ib, &nb) : NULL;

      const char *verdict;
      if (!a) {
         verdict = "GENERIC-FAIL";
         failed++;
      } else if (!b) {
         verdict = "spec-fail";
         failed_spec++;
      } else if (strcmp(sa, sb)) {
         verdict = "MISMATCH";
         mismatched++;
      } else {
         verdict = "match";
         matched++;
      }
      printf("%3u %-20s fc=%u/%u%s %-12s", i, e->function, num_fc, num_decl,
             unknown ? " (unknown names)" : "", verdict);
      if (a && b)
         printf(" instrs %u/%u scratch %u/%u", count_instrs(a), count_instrs(b),
                a->scratch_size, b->scratch_size);
      if (!a)
         printf(" %s", ea);
      else if (!b)
         printf(" spec: %s", eb);
      printf("\n");
      if (verbose || (a && b && strcmp(sa, sb))) {
         if (sa)
            printf("  generic (%u resources):\n%s", na, sa);
         if (sb)
            printf("  apple   (%u resources):\n%s", nb, sb);
      }
      ralloc_free(mctx);
   }
   printf("entries %u, specialized modules %u: match %u, MISMATCH %u, GENERIC-FAIL %u, "
          "spec-fail %u\n", num_entries, num_spec, matched, mismatched, failed, failed_spec);
   ralloc_free(ctx);
   glsl_type_singleton_decref();
   return !num_entries || mismatched || failed || failed_spec || num_spec != num_entries;
}
