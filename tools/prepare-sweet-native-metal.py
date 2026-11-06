#!/usr/bin/env python3
"""Adapt pinned HakiOS AIR/Gallium sources to Sweet; never touch a device.

The input is a disposable export of inputs.json's donor revision. Every edit
checks its source context so a changed donor cannot silently make another ABI.
"""
from pathlib import Path
import argparse
import re
import shutil

ROOT=Path(__file__).resolve().parents[1]
NATIVE=ROOT/'userspace/native-metal'

def replace(s,old,new,count=1):
    actual=s.count(old)
    if actual!=count:raise RuntimeError(f'expected {count} matches, found {actual}: {old[:110]!r}')
    return s.replace(old,new)

def adapt_backend(donor):
    air=donor/'tools/mesa/air'
    p=air/'nir_air.h';s=p.read_text()
    s=replace(s,'   unsigned argbuf_id;\n};',
                '   unsigned argbuf_id;\n   unsigned native_read_bytes; /* proved constant-offset UBO extent, zero = unknown */\n};')
    s=replace(s,'   uint64_t bits;      /* its value, as the bits of its type */',
                '   uint64_t bits;      /* little-endian packed scalar/small-vector value */\n   const char *name;    /* declared Metal function-constant name */')
    p.write_text(s)
    p=air/'air_metallib.c';s=p.read_text()
    s=replace(s,'   if (total > size || funcs + 4 > size || bc_off + bc_len > size) {',
                '   if (total > size || funcs > size || size - funcs < 4 || bc_off > size || bc_len > size - bc_off) {')
    s=replace(s,'   p += 4;\n   lib->functions =','''   p += 4;
   if (count > (size - funcs - 4) / 4) {
      *error = "function count exceeds container extent";return false;
   }
   lib->functions =''')
    s=replace(s,'      if (p + 4 > b + size) {','      if ((size_t)(b + size - p) < 4) {')
    s=replace(s,'      const uint8_t *q = p + 4, *end = p + len;\n      if (len < 4 || end > b + size) {',
                '      if (len < 4 || len > (size_t)(b + size - p)) {')
    s=replace(s,'      struct air_metallib_function *f = &lib->functions[lib->num_functions++];',
                '      const uint8_t *q = p + 4, *end = p + len;\n      struct air_metallib_function *f = &lib->functions[lib->num_functions++];')
    s=replace(s,'      while (q + 6 <= end && memcmp(q, "ENDT", 4) != 0) {',
                '      while ((size_t)(end - q) >= 6 && memcmp(q, "ENDT", 4) != 0) {')
    s=replace(s,'         if (v + vlen > end) {','         if (vlen > (size_t)(end - v)) {')
    s=replace(s,'      if (module_off + module_size > bc_len) {',
                '      if (module_off > bc_len || module_size > bc_len - module_off) {')
    p.write_text(s)
    p=air/'air_private.h';s=p.read_text()
    s=replace(s,'   AIR_VAL_PTR_SELECT,','''   AIR_VAL_PTR_SELECT,
   /* A translation-time pointer cell (air_pointer_cells.inc): .elems[0] is the
    * one stored resolution, or .cell_src a constant global's pointer
    * initializer, resolved where it is read. Never bytes in memory.
    */
   AIR_VAL_PTR_CELL,''')
    s=replace(s,'   unsigned num_elems;\n   struct air_val **elems;\n};',
                '   unsigned num_elems;\n   struct air_val **elems;\n   const struct air_value *cell_src;\n};')
    s=replace(s,'   const struct air_function *fn;\n','''   const struct air_function *fn;
   /* translating the static initializers -- and what they call, which is
    * cached and may be reused by the body: no shadow answers then */
   bool in_static_init;
   /* the executable edges of the function translate_body is in */
   struct air_edges *edges;
''')
    s=replace(s,'      bool skip;            /* llvm.* lists, and anything that failed */\n   } *global_plan;','''      bool skip;            /* llvm.* lists, and anything that failed */
      /* air_pointer_cells.inc */
      bool cell;            /* a constant wrapping one pointer: .cell_leaf */
      const struct air_value *cell_leaf;
      /* program-scope bytes known at translation time: plan_static_constants */
      uint8_t *shadow, *shadow_known;
      uint64_t shadow_size;
   } *global_plan;''')
    p.write_text(s)
    shutil.copy2(NATIVE/'backend/air_pointer_cells.inc',air/'air_pointer_cells.inc')
    shutil.copy2(NATIVE/'tests/air_specialization_check.c',air/'air_specialization_check.c')
    p=air/'meson.build';s=p.read_text()
    s=replace(s,"air2nir = executable(","""# Sweet: generic QuartzCore functions against Apple's own specializations
# (userspace/native-metal/tests/air_specialization_check.c). Host-only.
air_specialization_check = executable(
  'air_specialization_check',
  files('air_specialization_check.c'),
  dependencies : [dep_m, idep_air],
  include_directories : [inc_include, inc_src],
  build_by_default : false,
  install : false,
)

air2nir = executable(""")
    p.write_text(s)
    p=air/'air_to_nir.c';s=p.read_text()
    s=replace(s,'               (*out)[num++].index = idx;',
        '               const char *constant_name = md_str(node, 2);\n               (*out)[num].name = constant_name ? ralloc_strdup(mem_ctx, constant_name) : NULL;\n               (*out)[num++].index = idx;')
    s=replace(s,'   return v && (v->kind == AIR_VAL_PTR || v->kind == AIR_VAL_PTR_SELECT);',
                '   return v && (v->kind == AIR_VAL_PTR || v->kind == AIR_VAL_PTR_SELECT ||\n                v->kind == AIR_VAL_PTR_CELL);')
    s=replace(s,'/* -- instructions -','#include "air_pointer_cells.inc"\n\n/* -- instructions -')
    s=replace(s,'   if (base->kind == AIR_VAL_PTR_SELECT) {\n      struct air_val *a = NULL, *b = NULL;','''   if (base->kind == AIR_VAL_PTR_CELL) {
      if (!gep_indices_zero(in))
         return air_error(c, "instruction: getelementptr at an offset into a pointer cell");
      *out = base;
      return true;
   }
   if (base->kind == AIR_VAL_PTR_SELECT) {
      struct air_val *a = NULL, *b = NULL;''')
    s=replace(s,'   if (in->sub == AIR_CAST_BITCAST && dt->kind == AIR_TYPE_POINTER) {\n      struct air_val *v = air_get(c, in->operands[0]);','''   if ((in->sub == AIR_CAST_BITCAST || in->sub == AIR_CAST_ADDRSPACECAST) &&
       dt->kind == AIR_TYPE_POINTER) {
      struct air_val *v = air_get(c, in->operands[0]);
      if (v && (v->kind == AIR_VAL_PTR_CELL ||
                (v->kind == AIR_VAL_PTR_SELECT && in->sub == AIR_CAST_BITCAST))) {
         struct air_val *r = retype_pointer(c, v, dt->elem);
         if (!r)
            return air_error(c, "instruction: a bitcast of a choice with a side that is "
                                "not a pointer");
         air_set(c, &in->base, r);
         return true;
      }
   }
   if (in->sub == AIR_CAST_BITCAST && dt->kind == AIR_TYPE_POINTER) {
      struct air_val *v = air_get(c, in->operands[0]);''')
    s=replace(s,'         /* A choice between two members of an argument buffer cannot be kept','''         /* A condition known at translation time -- function constants, read
          * through the static initializer -- is no choice: take that side.
          */
         nir_def *known_cond = air_get_ssa(c, in->operands[0]);
         uint64_t take;
         if (!known_cond)
            return false;
         if (fold_scalar(c, known_cond, &take)) {
            air_set(c, &in->base, take ? pa : pb);
            return true;
         }
         /* A choice between two members of an argument buffer cannot be kept''')
    s=replace(s,'      /* A local holding a POINTER. Scratch memory cannot hold one',
        '      if (pointer_cell_alloca(c, in)) {\n         air_set(c, &in->base, air_val_cell(c, in->aux_type, NULL));\n         return true;\n      }\n      /* A local holding a POINTER. Scratch memory cannot hold one')
    s=replace(s,'   case AIR_OP_LOAD: {\n      struct air_val *p = air_get(c, in->operands[0]);\n      if (!p)\n         return false;\n','''   case AIR_OP_LOAD: {
      struct air_val *p = air_get(c, in->operands[0]);
      if (!p)
         return false;
      struct air_val *known = p->kind == AIR_VAL_PTR ? shadow_load(c, in, p->ptr) : NULL;
      if (known) {
         air_set(c, &in->base, known);
         return true;
      }
      if (p->kind == AIR_VAL_PTR_CELL ||
          (p->kind == AIR_VAL_PTR_SELECT && in->base.type->kind == AIR_TYPE_POINTER)) {
         if (in->base.type->kind != AIR_TYPE_POINTER)
            return air_error(c, "instruction: a number loaded from a pointer cell");
         struct air_val *v = load_pointer(c, p, in->base.type);
         if (!v)
            return false;
         air_set(c, &in->base, v);
         return true;
      }
''')
    s=replace(s,'      if (val_is_pointerish(sv))\n         return air_error(c, "instruction: a store of a pointer into memory, which this "','''      if (p->kind == AIR_VAL_PTR_CELL) {
         if (p->cell_src)
            return air_error(c, "instruction: a store into a constant pointer");
         if (p->elems[0])
            return air_error(c, "instruction: a second store into a pointer cell");
         if (sv->kind == AIR_VAL_PTR_CELL || !val_is_pointerish(sv))
            return air_error(c, "instruction: a pointer cell stored with something that is "
                                "not a resolution");
         p->elems[0] = sv;
         return true;
      }
      if (val_is_pointerish(sv))
         return air_error(c, "instruction: a store of a pointer into memory, which this "''')
    s=replace(s,'      return air_store_ptr(c, p->ptr, v, in->align);',
                '      shadow_store(c, in, p->ptr, v);\n      return air_store_ptr(c, p->ptr, v, in->align);')
    s=replace(s,'      } else if (g->addrspace == 3) {\n         /* a static threadgroup array */',
                '      } else if (g->is_const && g->init && plan_constant_global(c, pl, g)) {\n         /* air_pointer_cells.inc: constant data, or a pointer cell */\n      } else if (g->addrspace == 3) {\n         /* a static threadgroup array */')
    s=replace(s,'         c->scratch_size += air_type_alloc_size(c->m, g->value_type);\n      }\n   }\n}\n',
                '         c->scratch_size += air_type_alloc_size(c->m, g->value_type);\n      }\n   }\n   if (!c->error[0])\n      plan_static_constants(c);\n}\n')
    s=replace(s,'      if (pl->skip)\n         continue;\n      const struct air_global *g = c->m->globals[i];\n      struct air_ptr p = {','''      if (pl->skip)
         continue;
      const struct air_global *g = c->m->globals[i];
      if (pl->cell) {
         air_set(c, &g->base, air_val_cell(c, g->value_type, pl->cell_leaf));
         continue;
      }
      struct air_ptr p = {''')
    s=replace(s,'      if (!v)\n         return air_error(c, "interface: function constant %u has no value", ptr.binding), NULL;\n','''      if (!v) {
         /* Not supplied: what is read is the global's own initializer, which
          * for a Metal function constant is `undef` -- the uber shaders load
          * all 58 in their static initializer and guard each optional one
          * with is_function_constant_defined. Any other initializer is
          * refused rather than guessed.
          */
         bool declared_undef = false;
         for (unsigned g = 0; c->global_plan && g < c->m->num_globals; g++) {
            const struct air_global_plan *pl = &c->global_plan[g];
            const struct air_constant *k = air_value_as_constant(c->m->globals[g]->init);
            if (!pl->skip && pl->class == AIR_PTR_FUNCTION_CONSTANT &&
                pl->binding == ptr.binding)
               declared_undef = k && (k->ckind == AIR_CONST_UNDEF ||
                                      k->ckind == AIR_CONST_POISON);
         }
         if (!declared_undef)
            return air_error(c, "interface: function constant %u has no value", ptr.binding), NULL;
         res = nir_undef(&c->b, comps, bits);
         break;
      }
''')
    s=replace(s,'resolve_pointer_phi(struct air_ctx *c, const struct air_inst *in, const bool *reachable)',
                'resolve_pointer_phi(struct air_ctx *c, const struct air_inst *in,\n                    const struct air_edges *edges)')
    s=replace(s,'      if (!in->targets[k] || !reachable[in->targets[k]->index])\n         continue;',
                '      if (!in->targets[k] ||\n          !edge_possible(edges, in->targets[k]->index, in->parent->index))\n         continue;')
    s=replace(s,'   for (unsigned oi = 0; oi < num_order; oi++)\n      reachable[order[oi]] = true;\n',
                '   for (unsigned oi = 0; oi < num_order; oi++)\n      reachable[order[oi]] = true;\n   struct air_edges edges;\n   edges_init(c, &edges, f, reachable);\n   struct air_edges *outer_edges = c->edges;\n   c->edges = &edges;\n')
    s=replace(s,'      unsigned bi = order[oi];\n      const struct air_block *blk = &f->blocks[bi];\n      nir_block *nb = nir_block_of(c, blk);','''      unsigned bi = order[oi];
      const struct air_block *blk = &f->blocks[bi];
      if (!block_executable(&edges, bi)) {
         /* dead under the function constants: no instruction, no edge out */
         edges.processed[bi] = true;
         continue;
      }
      nir_block *nb = nir_block_of(c, blk);''')
    s=replace(s,'            if (!resolve_pointer_phi(c, in, reachable))','            if (!resolve_pointer_phi(c, in, &edges))')
    s=replace(s,'         struct hash_entry *e = _mesa_hash_table_search(c->phi_regs, in);\n         air_set(c, &in->base,\n','''         const struct air_value *only = single_incoming(&edges, in);
         if (only) {
            struct air_val *ov = air_get(c, only);
            if (!ov)
               return false;
            air_set(c, &in->base, ov);
            continue;
         }
         struct hash_entry *e = _mesa_hash_table_search(c->phi_regs, in);
         air_set(c, &in->base,
''')
    s=replace(s,'''         if (term->num_targets == 1) {
            nir_goto(&c->b, nir_block_of(c, term->targets[0]));
         } else {
            nir_def *cond = air_get_ssa(c, term->operands[0]);
            if (!cond)
               return false;
            if (term->targets[0] == term->targets[1])
               nir_goto(&c->b, nir_block_of(c, term->targets[0]));
            else
               nir_goto_if(&c->b, nir_block_of(c, term->targets[0]), cond,
                           nir_block_of(c, term->targets[1]));
         }''','''         if (term->num_targets == 1) {
            edge_take(&edges, bi, term->targets[0]);
            nir_goto(&c->b, nir_block_of(c, term->targets[0]));
         } else {
            nir_def *cond = air_get_ssa(c, term->operands[0]);
            uint64_t known = 0;
            if (!cond)
               return false;
            if (term->targets[0] == term->targets[1] || fold_scalar(c, cond, &known)) {
               const struct air_block *to = term->targets[0] == term->targets[1] || known
                                               ? term->targets[0] : term->targets[1];
               edge_take(&edges, bi, to);
               nir_goto(&c->b, nir_block_of(c, to));
            } else {
               edge_take(&edges, bi, term->targets[0]);
               edge_take(&edges, bi, term->targets[1]);
               nir_goto_if(&c->b, nir_block_of(c, term->targets[0]), cond,
                           nir_block_of(c, term->targets[1]));
            }
         }''')
    s=replace(s,'         nir_def *cond = air_get_ssa(c, term->operands[0]);\n         if (!cond)\n            return false;\n         nir_block *deflt = nir_block_of(c, term->targets[0]);','''         nir_def *cond = air_get_ssa(c, term->operands[0]);
         if (!cond)
            return false;
         uint64_t known;
         if (fold_scalar(c, cond, &known)) {
            const struct air_block *to = switch_known_target(term, known, cond->bit_size);
            if (to) {
               edge_take(&edges, bi, to);
               nir_goto(&c->b, nir_block_of(c, to));
               break;
            }
         }
         for (unsigned k = 0; k < term->num_targets; k++)
            edge_take(&edges, bi, term->targets[k]);
         nir_block *deflt = nir_block_of(c, term->targets[0]);''')
    s=replace(s,'      default:\n         return air_error(c, "instruction: block %u ends with opcode %u", bi, term->op);\n      }\n   }\n',
                '      default:\n         return air_error(c, "instruction: block %u ends with opcode %u", bi, term->op);\n      }\n      edges_decided(&edges, bi);\n   }\n   c->edges = outer_edges;\n   if (!edges_check(c, &edges))\n      return false;\n')
    s=replace(s,'            if (!ce)\n               continue; /* an edge from a block the entry cannot reach */\n',
                '            if (!ce)\n               continue; /* an edge from a block the entry cannot reach */\n            if (!edge_possible(&edges, in->targets[k]->index, bi))\n               continue; /* an edge the function constants rule out */\n')
    s=replace(s,'      if (!nir_src_is_const(nir_src_for_ssa(i)))\n         return air_error(c, "instruction: insertelement at a run-time index");','''      if (!nir_src_is_const(nir_src_for_ssa(i))) {
         /* each component compared against the index and selected: exact */
         air_set(c, &in->base,
                 air_val_ssa(c, nir_vector_insert(&c->b, v, x, nir_u2u32(&c->b, i))));
         return true;
      }''')
    s=replace(s,'static void\nrun_static_initializers(struct air_ctx *c)\n{\n',
                'static void\nrun_static_initializers(struct air_ctx *c)\n{\n   store_initializers(c);\n')
    s=replace(s,'   nir_cursor saved_consts = c->const_cursor;\n',
                '   nir_cursor saved_consts = c->const_cursor;\n   struct air_edges *saved_edges = c->edges;\n')
    s=replace(s,'   c->const_cursor = saved_consts;\n','   c->const_cursor = saved_consts;\n   c->edges = saved_edges;\n')
    s=replace(s,'   if (!c.error[0])\n      run_static_initializers(&c);\n',
                '   if (!c.error[0]) {\n      c.in_static_init = true;\n      run_static_initializers(&c);\n      c.in_static_init = false;\n   }\n')
    s=replace(s,'   nir_index_ssa_defs(nir_shader_get_entrypoint(c.nir));\n',
                '   drop_unread_scratch(c.nir);\n   nir_index_ssa_defs(nir_shader_get_entrypoint(c.nir));\n')
    s=replace(s,'      if (ptr && argv[i]->kind == AIR_VAL_PTR && argv[i]->ptr.offset) {',
                '      uint64_t const_offset;\n      if (ptr && argv[i]->kind == AIR_VAL_PTR && argv[i]->ptr.offset &&\n          !fold_scalar(c, argv[i]->ptr.offset, &const_offset)) {')
    s=replace(s,'         air_set(c, &f->args[i], argv[i]);   /* the caller\'s own resolution */','''         struct air_val *a = argv[i];
         if (a->kind == AIR_VAL_PTR && a->ptr.offset) {
            /* a constant byte offset is an immediate: rebuilt in this impl */
            struct air_ptr rp = a->ptr;
            uint64_t bytes = 0;
            fold_scalar(c, a->ptr.offset, &bytes);   /* checked by the caller above */
            rp.offset = nir_imm_int(&c->b, bytes);
            a = air_val_ptr(c, rp);
         }
         air_set(c, &f->args[i], a);   /* the caller's own resolution */''')
    s=replace(s,'         add_varying(c, name, slot, air_nir_components(t), node);',
                '         add_varying(c, md_key_str(node, "air.fragment_input"), slot, air_nir_components(t), node);')
    p.write_text(s)
    p=air/'air_intrinsics.c';s=p.read_text()
    s=replace(s,'   struct air_tex_name t;\n   if (!parse_tex_name(n->part[0], &t))\n','''   /* metal::texture2d() and its kin: a NULL handle, which is no binding. Every
    * member but is_null_texture is undefined on one, so a use that reaches
    * tex_handle is refused there; the QuartzCore uber shaders only phi it with
    * their texture arguments, on edges their function constants decide.
    */
   if (!strncmp(n->part[0], "get_null_", 9) && in->base.type->kind == AIR_TYPE_POINTER) {
      struct air_ptr p = { .class = AIR_PTR_NONE };
      air_set(c, &in->base, air_val_ptr(c, p));
      return true;
   }
   struct air_tex_name t;
   if (!parse_tex_name(n->part[0], &t))
''')
    p.write_text(s)
    shutil.copy2(NATIVE/'backend/air_live_resources.inc',air/'air_live_resources.inc')
    shutil.copy2(NATIVE/'backend/air_stage_link.inc',air/'air_stage_link.inc')
    shutil.copy2(NATIVE/'backend/air_vertex_exports.inc',air/'air_vertex_exports.inc')
    for name in ('sweet_native_ext.h','sweet_native_ext.c'):shutil.copy2(NATIVE/'backend'/name,air/name)
    p=air/'air_metal.h';s=p.read_text()
    s=replace(s,'\tunsigned fragment_buffer_table_count;\n};', '''\tunsigned fragment_buffer_table_count;
    /* Native ABI extension, shared by frontend and backend in this build. */
    float native_viewport[4];  /* x,y,width,height */
    unsigned native_scissor[4]; /* x,y,width,height */
    float native_blend[4];
    unsigned native_flags; /* bit0 viewport, bit1 scissor, bit2 blend */
    unsigned native_cull, native_winding, native_fill;
};''')
    p.write_text(s)
    p=air/'air_metal.c';s=p.read_text()
    s=replace(s,'#include "air_metal.h"','#include "sweet_native_ext.h"\n#include "util/u_framebuffer.h"\n#include <math.h>\n#include <time.h>\n#include "util/u_upload_mgr.h"')
    s=replace(s,'#include "nir_air.h"','#include "nir_air.h"\n#include "air_live_resources.inc"\n#include "air_stage_link.inc"\n#include "air_vertex_exports.inc"')
    # Metal ALWAYS enables primitive restart for indexed strip draws (0xFFFF /
    # 0xFFFFFFFF), the donor turned it off ("Metal has no restart index"). The
    # restart value then counted as vertex 65535 in index_range, the vertex
    # stream bounds check refused the whole draw, and every stroked path
    # CoreAnimation draws as a restart-separated strip vanished (Control Centre
    # glyph outlines, status-bar battery outline, battery widget ring).
    s=replace(s,"""static void
index_range(const uint8_t *indices, unsigned count, unsigned index_size, unsigned *min_out,
            unsigned *max_out)
{
   unsigned min = UINT32_MAX, max = 0;""","""static void
index_range(const uint8_t *indices, unsigned count, unsigned index_size, bool restart,
            unsigned *min_out, unsigned *max_out)
{
   const unsigned restart_index = index_size == 2 ? 0xffffu : 0xffffffffu;
   unsigned min = UINT32_MAX, max = 0;""")
    s=replace(s,"""         memcpy(&u, indices + (size_t)i * 4, sizeof(u));
         v = u;
      }
      if (v < min)""","""         memcpy(&u, indices + (size_t)i * 4, sizeof(u));
         v = u;
      }
      if (restart && v == restart_index)
         continue;
      if (v < min)""")
    s=replace(s,"""   *min_out = min;
   *max_out = max;
}""","""   if (min > max)
      min = max = 0;   /* nothing but restarts: draws nothing */
   *min_out = min;
   *max_out = max;
}""")
    s=replace(s,"""      index_range((const uint8_t *)rwa_buffer_map(draw->index_buffer) +
                  draw->index_buffer_offset, draw->vertex_count, index_size, &min_index,
                  &max_index);""","""      index_range((const uint8_t *)rwa_buffer_map(draw->index_buffer) +
                  draw->index_buffer_offset, draw->vertex_count, index_size,
                  prim == MESA_PRIM_LINE_STRIP || prim == MESA_PRIM_TRIANGLE_STRIP,
                  &min_index, &max_index);""")
    s=replace(s,"""      struct pipe_draw_info info = {
         .mode = prim, .index_bounds_valid = true,
         .index_size = (uint16_t)index_size,""","""      const bool strip = prim == MESA_PRIM_LINE_STRIP || prim == MESA_PRIM_TRIANGLE_STRIP;
      struct pipe_draw_info info = {
         .mode = prim, .index_bounds_valid = true,
         .index_size = (uint16_t)index_size,
         .primitive_restart = index_size && strip,
         .restart_index = index_size == 2 ? 0xffffu : 0xffffffffu,""")
    # Metal lines are one pixel wide; the donor's rasterizer state left
    # line_width at 0 and freedreno programs GRAS_SU_CNTL.LINEHALFWIDTH from it,
    # so every MTLPrimitiveTypeLine draw rasterised nothing. CoreAnimation
    # draws the edges of vector shapes as lines into coverage masks
    # (com.apple.coreanimation.draw.*Tcma*Lln* pipelines, prim 1 in the draw
    # log): Control Centre glyphs, status-bar battery outline, widget rings.
    s=replace(s,"""      &(struct pipe_rasterizer_state){
         .cull_face = PIPE_FACE_NONE, .half_pixel_center = 1, .bottom_edge_rule = 1,
""","""      &(struct pipe_rasterizer_state){
         .cull_face = PIPE_FACE_NONE, .half_pixel_center = 1, .bottom_edge_rule = 1,
         .line_width = 1.0f, .point_size = 1.0f,
""")
    # Diagnostics for missing Control Centre vector glyphs (2026-11-02): the
    # first uploads into one-byte textures (CoreAnimation's coverage masks)
    # with their geometry and how many texels are non-zero.
    s=replace(s,'''   texture->device->pipe->texture_subdata(texture->device->pipe, texture->res, level,
                                          PIPE_MAP_WRITE, &box, bytes, row,
                                          row * box.height);
   return 0;
}''','''   if (util_format_get_blocksize(texture->res->format) == 1) {
      static unsigned logged;
      if (logged < 48) {
         unsigned nonzero = 0, maxv = 0;
         for (int y = 0; y < box.height; y++)
            for (int x = 0; x < box.width; x++) {
               unsigned v = ((const uint8_t *)bytes)[(size_t)y * row + x];
               nonzero += v != 0;
               maxv = v > maxv ? v : maxv;
            }
         logged++;
         fprintf(stderr, "SweetNativeMetal: 1-byte upload %s %ux%u level %u region %d,%d %dx%d stride %zu nonzero %u max %u\\n",
                 texture->format_name, texture->res->width0, texture->res->height0, level,
                 box.x, box.y, box.width, box.height, row, nonzero, maxv);
      }
   }
   if (texture->res->format == PIPE_FORMAT_R16G16B16A16_FLOAT) {
      static unsigned logged16;
      if (logged16 < 24) {
         logged16++;
         const uint16_t *h = (const uint16_t *)bytes;
         const unsigned w = box.width, last = w ? w - 1 : 0;
         const unsigned at[4] = { 0, w / 4, w / 2, last };
         char vals[256] = "";
         for (unsigned k = 0; k < 4; k++) {
            const uint16_t *t = h + (size_t)at[k] * 4;
            size_t used = strlen(vals);
            snprintf(vals + used, sizeof(vals) - used, " [%u]=%.3f,%.3f,%.3f,%.3f", at[k],
                     _mesa_half_to_float(t[0]), _mesa_half_to_float(t[1]),
                     _mesa_half_to_float(t[2]), _mesa_half_to_float(t[3]));
         }
         fprintf(stderr, "SweetNativeMetal: f16 upload %ux%u region %d,%d %dx%d stride %zu%s\\n",
                 texture->res->width0, texture->res->height0, box.x, box.y, box.width,
                 box.height, row, vals);
      }
   }
   texture->device->pipe->texture_subdata(texture->device->pipe, texture->res, level,
                                          PIPE_MAP_WRITE, &box, bytes, row,
                                          row * box.height);
   return 0;
}''')
    s=replace(s,'   slot_shapes(fs, render->frag_slots, render->frag_compare_sampler);',
                '   native_prune_direct_resources(fs, &render->finfo);\n   slot_shapes(fs, render->frag_slots, render->frag_compare_sampler);')
    s=replace(s,'      if (render->vinfo.stage != MESA_SHADER_VERTEX) {',
                '      native_prune_direct_resources(vs, &render->vinfo);\n      if (render->vinfo.stage != MESA_SHADER_VERTEX) {')
    s=replace(s,'   if (compute->info.stage != MESA_SHADER_COMPUTE) {',
                '   native_prune_direct_resources(compute->nir, &compute->info);\n   if (compute->info.stage != MESA_SHADER_COMPUTE) {')
    s=replace(s,'   render->fs_cso = make_stage_cso(device, fs, why, why_size);',
                '   if (!render->generated_vertex && !native_link_stages(vs, &render->vinfo, fs, &render->finfo, why, why_size))\n      goto fail;\n   render->fs_cso = make_stage_cso(device, fs, why, why_size);')
    # Gallium frontends must run the screen's NIR finalizer before creating
    # shader state. ir3's later post-finalizer does not replace this callback.
    s=replace(s,'static void\nfinalize_for_gallium(nir_shader *nir, struct pipe_screen *screen)\n{',
                'static bool\nfinalize_for_gallium(nir_shader *nir, struct pipe_screen *screen, char *why, size_t why_size)\n{')
    s=replace(s,'   NIR_PASS(_, nir, nir_lower_io, nir_var_shader_in | nir_var_shader_out,\n            type_size_vec4, nir_lower_io_use_interpolated_input_intrinsics);\n}',
                '   nir_shader_gather_info(nir, nir_shader_get_entrypoint(nir));\n   if (screen->finalize_nir) {\n      char *error = screen->finalize_nir(screen, nir);\n      if (error) {say(why, why_size, "NIR finalization: %s", error);free(error);return false;}\n   }\n   NIR_PASS(_, nir, nir_lower_io, nir_var_shader_in | nir_var_shader_out,\n            type_size_vec4, nir_lower_io_use_interpolated_input_intrinsics);\n   native_widen_vertex_exports(nir);\n   nir_shader_gather_info(nir, nir_shader_get_entrypoint(nir));\n   return true;\n}')
    s=replace(s,'   finalize_for_gallium(cs, device->screen);',
                '   if (!finalize_for_gallium(cs, device->screen, why, why_size)) {ralloc_free(cs);return NULL;}')
    s=replace(s,'   finalize_for_gallium(clone, device->screen);',
                '   if (!finalize_for_gallium(clone, device->screen, why, why_size)) {ralloc_free(clone);return NULL;}')
    # create_sampler_view gives the frontend a reference. Gallium may retain
    # additional references in its context/state caches; only the final release
    # may invoke sampler_view_destroy.
    s=replace(s,'   if (texture->view)\n      texture->device->pipe->sampler_view_destroy(texture->device->pipe, texture->view);',
                '   pipe_sampler_view_reference(&texture->view, NULL);')
    # Texture storage/format is immutable here. Keep its level-zero surface
    # instead of allocating an identical Gallium view for every CA draw.
    s=replace(s,'   struct pipe_sampler_view *view;  /* made when a dispatch first samples it, then kept */',
                '   struct pipe_sampler_view *view;  /* made when a dispatch first samples it, then kept */\n   struct pipe_surface *native_surface;')
    s=replace(s,'   pipe_resource_reference(&texture->res, NULL);\n   free(texture);',
                '   pipe_surface_reference(&texture->native_surface, NULL);\n   pipe_resource_reference(&texture->res, NULL);\n   free(texture);')
    s=replace(s,'rwa_render_draw(struct rwa_render *render, const struct rwa_draw *draw,',
                'native_render_draw(struct rwa_render *render, const struct rwa_draw *draw,')
    # Return one transient reference so existing draw cleanup stays balanced.
    s=replace(s,'surf[i] = pipe->create_surface(pipe, draw->color[i]->res,\n         &(struct pipe_surface){ .format = draw->color[i]->res->format });',
                'surf[i] = native_render_surface(draw->color[i]);')
    s=replace(s,'zsurf = pipe->create_surface(pipe, draw->depth->res,\n         &(struct pipe_surface){ .format = draw->depth->res->format });',
                'zsurf = native_render_surface(draw->depth);')
    s=replace(s,'int\nnative_render_draw(', 'static int\nnative_render_draw(')
    at=s.index('static int\nnative_render_draw(')
    s=s[:at]+'''static struct pipe_surface *
native_render_surface(struct rwa_texture *texture)
{
   if (!texture->native_surface)
      texture->native_surface = texture->device->pipe->create_surface(texture->device->pipe,
         texture->res, &(struct pipe_surface){ .format = texture->res->format });
   struct pipe_surface *surface = NULL;
   if (texture->native_surface) pipe_surface_reference(&surface, texture->native_surface);
   return surface;
}

'''+s[at:]
    s=replace(s,'   bool software;','''   bool software;
   bool in_command, lost;
   uint64_t native_begin_ns, native_window_ns, native_draw_ns, native_encode_ns, native_finish_ns;
   uint64_t native_draw_count, native_command_count, native_phase_ns[4];
   struct pipe_constant_buffer native_cb[PIPE_SHADER_TYPES][PIPE_MAX_CONSTANT_BUFFERS];
   bool native_cb_valid[PIPE_SHADER_TYPES][PIPE_MAX_CONSTANT_BUFFERS], native_buffer_writes;
   uint64_t native_cb_calls, native_cb_reused, native_cb_ns;
   struct {
      const void *source;
      unsigned bytes;
      uint64_t generation;
      struct pipe_constant_buffer cb;
   } native_uploads[256];
   unsigned native_upload_count;
   uint64_t native_upload_generation, native_upload_hits, native_upload_misses, native_upload_ns, native_upload_bytes;
   uint64_t native_uniform_trimmed, native_uniform_trim_bytes;
   struct pipe_sampler_view *native_views[PIPE_SHADER_TYPES][RWA_MAX_TEXTURES];
   struct pipe_image_view native_images[PIPE_SHADER_TYPES][RWA_MAX_TEXTURES];
   void *native_samplers[PIPE_SHADER_TYPES][RWA_MAX_TEXTURES];
   bool native_views_valid[PIPE_SHADER_TYPES], native_images_valid[PIPE_SHADER_TYPES], native_samplers_valid[PIPE_SHADER_TYPES];
   uint64_t native_views_ns, native_images_ns, native_samplers_ns, native_binding_reused;
   unsigned native_pending_views[PIPE_SHADER_TYPES], native_pending_images[PIPE_SHADER_TYPES];
   unsigned native_pending_samplers[PIPE_SHADER_TYPES];
   struct pipe_framebuffer_state batch_fb;
   struct rwa_constant constants[3][256];
   unsigned constant_count[3];''')
    s=replace(s,'/* PRINTFLIKE,', '''static unsigned native_constants(struct rwa_device *, unsigned, void *, const void *,
                                  size_t, struct air_constant_value **);

/* PRINTFLIKE,''')
    # All command work retains its resources until command_end. Avoid clearing
    # sampler/image bindings between adjacent draws; clear the union at the end.
    s=replace(s,'   struct pipe_context *pipe = device->pipe;\n   if (bound->num_views)','''   if (device->in_command) {
      device->native_pending_views[stage]=MAX2(device->native_pending_views[stage],bound->num_views);
      device->native_pending_images[stage]=MAX2(device->native_pending_images[stage],bound->num_images);
      device->native_pending_samplers[stage]=MAX2(device->native_pending_samplers[stage],bound->num_samplers);
      return;
   }
   struct pipe_context *pipe = device->pipe;
   if (bound->num_views)''')
    # Clear stale trailing slots when a new shader uses fewer resources. The
    # remaining bindings are reused, not blindly unbound and rebound per draw.
    s=replace(s,'   if (num_views)\n      pipe->set_sampler_views(pipe, stage, 0, num_views, 0, false, views);','''   unsigned old_views=device->native_pending_views[stage];
   if (device->in_command && device->native_views_valid[stage] &&
       !memcmp(device->native_views[stage],views,sizeof(views)))device->native_binding_reused++;
   else {
      uint64_t began=native_now_ns();
      if (num_views || old_views)
         pipe->set_sampler_views(pipe, stage, 0, num_views,
            old_views>num_views?old_views-num_views:0, false, views);
      device->native_views_ns+=native_now_ns()-began;
      memcpy(device->native_views[stage],views,sizeof(views));device->native_views_valid[stage]=true;
   }''')
    s=replace(s,'   if (num_images)\n      pipe->set_shader_images(pipe, stage, 0, num_images, 0, images);','''   unsigned old_images=device->native_pending_images[stage];
   if (device->in_command && device->native_images_valid[stage] &&
       !memcmp(device->native_images[stage],images,sizeof(images)))device->native_binding_reused++;
   else {
      uint64_t began=native_now_ns();
      if (num_images || old_images)
         pipe->set_shader_images(pipe, stage, 0, num_images,
            old_images>num_images?old_images-num_images:0, images);
      device->native_images_ns+=native_now_ns()-began;
      memcpy(device->native_images[stage],images,sizeof(images));device->native_images_valid[stage]=true;
   }''')
    s=replace(s,'   if (num_samplers)\n      pipe->bind_sampler_states(pipe, stage, 0, num_samplers, samplers);','''   unsigned sampler_slots=MAX2(num_samplers,device->native_pending_samplers[stage]);
   if (device->in_command && device->native_samplers_valid[stage] &&
       !memcmp(device->native_samplers[stage],samplers,sizeof(samplers)))device->native_binding_reused++;
   else {
      uint64_t began=native_now_ns();
      if (sampler_slots)pipe->bind_sampler_states(pipe, stage, 0, sampler_slots, samplers);
      device->native_samplers_ns+=native_now_ns()-began;
      memcpy(device->native_samplers[stage],samplers,sizeof(samplers));device->native_samplers_valid[stage]=true;
   }''')
    s=replace(s,'static int\nnative_render_draw(','''static uint64_t native_now_ns(void);
static void native_draw_checkpoint(struct rwa_device *d,uint64_t *at,unsigned phase)
{
   uint64_t now=native_now_ns();d->native_phase_ns[phase]+=now-*at;*at=now;
}
static int
native_render_draw(''')
    s=replace(s,'   const char *fname = render->finfo.entry_point ? render->finfo.entry_point',
                '   uint64_t native_phase_at=native_now_ns();\n   const char *fname = render->finfo.entry_point ? render->finfo.entry_point')
    s=replace(s,'   float znear = draw->depth_range ? draw->depth_range[0] : 0.0f;',
                '   native_draw_checkpoint(device,&native_phase_at,0);\n   float znear = draw->depth_range ? draw->depth_range[0] : 0.0f;')
    s=replace(s,'   struct rwa_bound fbound = { 0 }, vbound = { 0 };',
                '   native_draw_checkpoint(device,&native_phase_at,1);\n   struct rwa_bound fbound = { 0 }, vbound = { 0 };')
    s=replace(s,'   /* clear_color NULL is MTLLoadActionLoad:',
                '   native_draw_checkpoint(device,&native_phase_at,2);\n   /* clear_color NULL is MTLLoadActionLoad:')
    s=replace(s,'unbind:\n   unbind_stage(device, PIPE_SHADER_FRAGMENT, &fbound);',
                'unbind:\n   native_draw_checkpoint(device,&native_phase_at,3);\n   unbind_stage(device, PIPE_SHADER_FRAGMENT, &fbound);')
    # Gallium constant bindings persist. Do not re-upload an unchanged unaligned
    # user constant range for every adjacent CA draw in the same transaction.
    s=replace(s,'static int\nbind_stage(','''static uint64_t native_now_ns(void);
static void native_invalidate_constants(struct rwa_device *d)
{
   memset(d->native_cb_valid,0,sizeof(d->native_cb_valid));
   d->native_upload_generation++;
}
static void
native_bind_constant(struct rwa_device *d, enum pipe_shader_type stage,unsigned slot,
                     const struct pipe_constant_buffer *cb)
{
   d->native_cb_calls++;
   struct pipe_constant_buffer resolved=*cb;
   if(d->in_command && cb->user_buffer && cb->buffer_size) {
      unsigned i=0;
      while(i<d->native_upload_count &&
         !(d->native_uploads[i].generation==d->native_upload_generation &&
           d->native_uploads[i].source==cb->user_buffer && d->native_uploads[i].bytes==cb->buffer_size))i++;
      if(i<d->native_upload_count) {
         resolved=d->native_uploads[i].cb;d->native_upload_hits++;
      } else if(i<ARRAY_SIZE(d->native_uploads)) {
         uint64_t began=native_now_ns();
         struct pipe_constant_buffer uploaded={.buffer_size=cb->buffer_size};
         // Exactly freedreno's existing user-constant upload path, but retain
         // its aligned GPU range for other slots/stages/draws in this command.
         u_upload_data(d->pipe->stream_uploader,0,cb->buffer_size,64,cb->user_buffer,
                       &uploaded.buffer_offset,&uploaded.buffer);
         d->native_upload_ns+=native_now_ns()-began;
         if(uploaded.buffer) {
            d->native_uploads[i].source=cb->user_buffer;d->native_uploads[i].bytes=cb->buffer_size;
            d->native_uploads[i].generation=d->native_upload_generation;d->native_uploads[i].cb=uploaded;
            d->native_upload_count++;d->native_upload_misses++;d->native_upload_bytes+=cb->buffer_size;
            resolved=uploaded;
         }
         // If allocation fails or the bounded cache is full, the original
         // driver path still handles this binding. Never return invented data.
      }
   }
   cb=&resolved;
   const struct pipe_constant_buffer *old=&d->native_cb[stage][slot];
   if(d->in_command && d->native_cb_valid[stage][slot] &&
      old->buffer==cb->buffer && old->user_buffer==cb->user_buffer &&
      old->buffer_offset==cb->buffer_offset && old->buffer_size==cb->buffer_size) {
      d->native_cb_reused++;return;
   }
   uint64_t began=native_now_ns();
   d->pipe->set_constant_buffer(d->pipe,stage,slot,false,cb);
   d->native_cb_ns+=native_now_ns()-began;
   d->native_cb[stage][slot]=*cb;d->native_cb_valid[stage][slot]=true;
}

static int
bind_stage(''')
    s=replace(s,'         pipe->set_constant_buffer(pipe, stage, r->index, false, &cb);',
                '         native_bind_constant(device,stage,r->index,&cb);')
    s=replace(s,'         if (r->write)\n            writable |= 1u << r->index;','''         if (r->write) {
            writable |= 1u << r->index;
            device->native_buffer_writes=true;
            native_invalidate_constants(device);
         }''')
    s=replace(s,'   const char *name = compute->info.entry_point ? compute->info.entry_point',
                '   native_invalidate_constants(device);\n   const char *name = compute->info.entry_point ? compute->info.entry_point')
    s=replace(s,'            size_t want = r->size && r->size <= left ? r->size : left;','''            size_t want = r->size && r->size <= left ? r->size : left;
            if(r->native_read_bytes && r->native_read_bytes<want) {
               device->native_uniform_trimmed++;
               device->native_uniform_trim_bytes+=want-r->native_read_bytes;
               want=r->native_read_bytes;
            }''')
    # GPU-only build. Keeping a CPU fallback here would disguise failed bring-up.
    s=replace(s,'   bool software = debug_get_bool_option("RWA_SOFTWARE", false);','   bool software = false;')
    s=replace(s,'   if (!device->screen)\n      device->screen = sw_screen_create(null_sw_create());','')
    s=s.replace('#include "target-helpers/inline_sw_helper.h"','').replace('#include "null/null_sw_winsys.h"','')
    s=replace(s,'      air_module_function_constants(compute->mem_ctx, bitcode, size, &constants);',
                '      native_constants(device, 2, compute->mem_ctx, bitcode, size, &constants);')
    s=replace(s,'      air_module_function_constants(mem_ctx, bitcode, size, &constants);',
                '      native_constants(device, 2, mem_ctx, bitcode, size, &constants);',count=2)
    s=replace(s,'      air_module_function_constants(render->mem_ctx, fragment_bitcode, fragment_size, &ffc);',
                '      native_constants(device, 1, render->mem_ctx, fragment_bitcode, fragment_size, &ffc);')
    s=replace(s,'         air_module_function_constants(render->mem_ctx, vertex_bitcode, vertex_size, &vfc);',
                '         native_constants(device, 0, render->mem_ctx, vertex_bitcode, vertex_size, &vfc);')
    s=replace(s,'   pipe->launch_grid(pipe, &grid);','   pipe->launch_grid(pipe, &grid);\n   pipe->memory_barrier(pipe, PIPE_BARRIER_ALL);')
    s=replace(s,'   int rc = finish_or_say_why(device, true, "dispatch", why, why_size);',
                '   int rc = device->in_command ? 0 : finish_or_say_why(device, true, "dispatch", why, why_size);')
    s=replace(s,'   rc = finish_or_say_why(device, true, "draw", why, why_size);',
                '   rc = device->in_command ? 0 : finish_or_say_why(device, true, "draw", why, why_size);')
    s=replace(s,'   pipe->set_framebuffer_state(pipe, &fb);', '''   bool same = device->in_command && device->batch_fb.width == fb.width &&
      device->batch_fb.height == fb.height && device->batch_fb.samples == fb.samples &&
      device->batch_fb.nr_cbufs == fb.nr_cbufs;
   for (unsigned i=0; same && i<fb.nr_cbufs; i++)
      same = device->batch_fb.cbufs[i] &&
             device->batch_fb.cbufs[i]->texture == fb.cbufs[i]->texture;
   if (same) same = (!fb.zsbuf && !device->batch_fb.zsbuf) ||
      (fb.zsbuf && device->batch_fb.zsbuf && fb.zsbuf->texture == device->batch_fb.zsbuf->texture);
   if (!same) {
      pipe->set_framebuffer_state(pipe, &fb);
      if(device->in_command)util_copy_framebuffer_state(&device->batch_fb,&fb);
   }''')
    s=replace(s,'   pipe->set_framebuffer_state(pipe, &(struct pipe_framebuffer_state){ 0 });',
                '   if (!device->in_command) pipe->set_framebuffer_state(pipe, &(struct pipe_framebuffer_state){ 0 });')
    # Context and batch_fb own references when a draw is deferred.
    s=s.replace('pipe->surface_destroy(pipe, surf[i]);','pipe_surface_reference(&surf[i], NULL);')
    s=s.replace('pipe->surface_destroy(pipe, surf[j]);','pipe_surface_reference(&surf[j], NULL);')
    s=replace(s,'      pipe->surface_destroy(pipe, zsurf);','      pipe_surface_reference(&zsurf, NULL);')
    s=replace(s,'   if (!done) {\n      say(why, why_size,', '   if (!done) {\n      device->lost = true;\n      say(why, why_size,')
    # Do not destroy a timed-out context and its in-flight allocations.
    marker='rwa_device_close(struct rwa_device *device)\n{'
    s=replace(s,marker,marker+'\n   if(device && device->lost)return;\n   if(device)for(unsigned k=0;k<3;k++)for(unsigned i=0;i<device->constant_count[k];i++)free((void*)device->constants[k][i].name);')
    s=replace(s,'.depth_clip_near = 1, .depth_clip_far = 1, .clip_halfz = zs_format != 0,',
                '.depth_clip_near = 1, .depth_clip_far = 1, .clip_halfz = 1, .scissor = 1,')
    s=replace(s,'   /* The attachments of the pass must be the attachments the pipeline was compiled for:','''   if(device->lost) {say(why,why_size,"device lost");return -1;}
   if(draw->native_flags&1)for(unsigned i=0;i<4;i++) {
      if(!isfinite(draw->native_viewport[i]) || (i>=2 && draw->native_viewport[i]<0)) {
         say(why,why_size,"invalid viewport");return -1;
      }
   }
   /* The attachments of the pass must be the attachments the pipeline was compiled for:''')
    s=replace(s,'   pipe->set_viewport_states(pipe, 0, 1, &(struct pipe_viewport_state){', '''   float vx=0, vy=0, vw=w, vh=h;
   if(draw->native_flags&1) {
      vx=draw->native_viewport[0];vy=draw->native_viewport[1];
      vw=draw->native_viewport[2];vh=draw->native_viewport[3];
   }
   pipe->set_viewport_states(pipe, 0, 1, &(struct pipe_viewport_state){''')
    s=replace(s,'      .scale = { w / 2.0f, -(h / 2.0f), zfar - znear },\n      .translate = { w / 2.0f, h / 2.0f, znear } });', '''      .scale = { vw / 2.0f, -(vh / 2.0f), zfar - znear },
      .translate = { vx + vw / 2.0f, vy + vh / 2.0f, znear } });
   struct pipe_scissor_state scissor={.minx=0,.miny=0,.maxx=w,.maxy=h};
   if(draw->native_flags&2) {
      scissor.minx=MIN2(draw->native_scissor[0],w);scissor.miny=MIN2(draw->native_scissor[1],h);
      scissor.maxx=scissor.minx+MIN2(draw->native_scissor[2],w-scissor.minx);
      scissor.maxy=scissor.miny+MIN2(draw->native_scissor[3],h-scissor.miny);
   }
   pipe->set_scissor_states(pipe,0,1,&scissor);
   pipe->set_blend_color(pipe,&(struct pipe_blend_color){.color={draw->native_blend[0],draw->native_blend[1],draw->native_blend[2],draw->native_blend[3]}});''')
    # Cull/fill are explicitly checked by frontend; don't silently ignore them.
    s+='\n#include "sweet_native_ext.c"\n'
    p.write_text(s)

# Asynchronous command completion. -commit hands the transaction to the GPU and
# returns; this thread polls the kernel fence (no Gallium access, no object
# lock while waiting) and completes command buffers in commit order. Completed
# handlers run outside the object lock: a handler that takes a CoreAnimation
# lock must not be able to deadlock against a render thread inside Metal.
NATIVE_COMPLETION=r'''
struct native_completion {
    struct native_completion *next;
    id commandBuffer;
    struct rwa_device *device;
    uint64_t fence, queued_ns;
};
static pthread_mutex_t native_completion_lock=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t native_completion_ready=PTHREAD_COND_INITIALIZER;
static struct native_completion *native_completion_head,*native_completion_tail;
static uint64_t native_completion_now(void)
{
    struct timespec now;clock_gettime(CLOCK_MONOTONIC_RAW,&now);
    return (uint64_t)now.tv_sec*1000000000ull+now.tv_nsec;
}
static struct {
    uint64_t window,count,latency,latency_max,polls,handlers,failures;
} native_completion_stats;
static void native_completion_finish(struct native_completion *item)
{
    @autoreleasepool {
        char why[512]={0};
        const uint64_t began=native_completion_now();
        int rc;
        /* Each poll takes the kernel's GPU lock, which the render thread's
         * submits need too: back off 250 us -> 2 ms instead of a fixed 200 us
         * (CA commands run ~15-25 ms on the GPU). */
        useconds_t pause=250;
        for(;;) {
            const bool give_up=native_completion_now()-began>=2000000000ull;
            rc=rwa_command_poll(item->device,item->fence,give_up,why,sizeof why);
            native_completion_stats.polls++;
            if(rc!=1)break;
            usleep(pause);
            if(pause<2000)pause*=2;
        }
        if(rc)rw_print("command buffer completion: %s\n",why);
        dispatch_queue_t queue=NULL;
        id commandBuffer=item->commandBuffer;
        NSArray *completed=[(RedwoodMetalCommandBuffer *)commandBuffer nativeTakeCompleted:rc why:why queue:&queue];
        for(id handler in completed) {
            void (^block)(id)=(void (^)(id))handler;
            if(queue)dispatch_async(queue,^{block(commandBuffer);});
            else block(commandBuffer);
            native_completion_stats.handlers++;
        }
        [completed release];
        if(queue)dispatch_release(queue);
        const uint64_t done=native_completion_now(),waited=done-item->queued_ns;
        native_completion_stats.count++;native_completion_stats.failures+=rc!=0;
        native_completion_stats.latency+=waited;
        if(waited>native_completion_stats.latency_max)native_completion_stats.latency_max=waited;
        if(!native_completion_stats.window)native_completion_stats.window=done;
        if(done-native_completion_stats.window>=5000000000ull) {
            const uint64_t n=native_completion_stats.count;
            fprintf(stderr,"SweetNativeMetal: async completion window_ms=%llu completed=%llu latency_avg_us=%llu latency_max_us=%llu polls=%llu handlers=%llu failures=%llu wait_until_completed=%llu\n",
                (unsigned long long)((done-native_completion_stats.window)/1000000),(unsigned long long)n,
                (unsigned long long)(n?native_completion_stats.latency/n/1000:0),
                (unsigned long long)(native_completion_stats.latency_max/1000),
                (unsigned long long)native_completion_stats.polls,(unsigned long long)native_completion_stats.handlers,
                (unsigned long long)native_completion_stats.failures,
                (unsigned long long)atomic_exchange(&native_wait_until_completed,0));
            memset(&native_completion_stats,0,sizeof native_completion_stats);
            native_completion_stats.window=done;
        }
        [commandBuffer release];
    }
}
static void *native_completion_main(void *unused)
{
    (void)unused;
    pthread_setname_np("SweetNativeMetal completion");
    for(;;) {
        pthread_mutex_lock(&native_completion_lock);
        while(!native_completion_head)pthread_cond_wait(&native_completion_ready,&native_completion_lock);
        struct native_completion *item=native_completion_head;
        native_completion_head=item->next;
        if(!native_completion_head)native_completion_tail=NULL;
        pthread_mutex_unlock(&native_completion_lock);
        native_completion_finish(item);
        free(item);
    }
    return NULL;
}
/* Called once, under the object lock, by the first commit. */
static bool native_async_commits(void)
{
    static int enabled=-1;
    if(enabled>=0)return enabled;
    enabled=0;
    if(!access("/var/local/sweet-native-sync-commit",F_OK)) {
        rw_print("command buffers wait for the GPU at commit (/var/local/sweet-native-sync-commit)\n");
        return false;
    }
    pthread_attr_t attr;pthread_t thread;
    pthread_attr_init(&attr);
    pthread_attr_setdetachstate(&attr,PTHREAD_CREATE_DETACHED);
    pthread_attr_set_qos_class_np(&attr,QOS_CLASS_USER_INTERACTIVE,0);
    if(pthread_create(&thread,&attr,native_completion_main,NULL))
        rw_print("no completion thread; command buffers wait for the GPU at commit\n");
    else {
        enabled=1;
        rw_print("command buffers complete asynchronously on a completion thread "
                 "(create /var/local/sweet-native-sync-commit and restart backboardd to wait at commit)\n");
    }
    pthread_attr_destroy(&attr);
    return enabled;
}
static void native_completion_enqueue(id commandBuffer,struct rwa_device *device,uint64_t fence)
{
    struct native_completion *item=calloc(1,sizeof *item);
    if(!item) {
        /* No memory for the queue: complete it here, as a synchronous commit. */
        struct native_completion local={NULL,[commandBuffer retain],device,fence,native_completion_now()};
        native_completion_finish(&local);
        return;
    }
    item->commandBuffer=[commandBuffer retain];item->device=device;
    item->fence=fence;item->queued_ns=native_completion_now();
    pthread_mutex_lock(&native_completion_lock);
    if(native_completion_tail)native_completion_tail->next=item;
    else native_completion_head=item;
    native_completion_tail=item;
    pthread_cond_signal(&native_completion_ready);
    pthread_mutex_unlock(&native_completion_lock);
}
'''

def adapt_frontend(donor):
    src=donor/'userland/src'
    p=src/'redwoodmetal.m';s=p.read_text()
    begin=s.index('struct rwa_device;')
    end=s.index('_Static_assert(',begin)
    s=s[:begin]+'''#include "sweet_native_ext.h"
#include <pthread.h>
#include <pthread/qos.h>
#include <stdatomic.h>
#include <unistd.h>
#include <math.h>

static bool native_async_commits(void);
static void native_completion_enqueue(id commandBuffer,struct rwa_device *device,uint64_t fence);
static _Atomic unsigned long long native_wait_until_completed;

'''+s[end:]
    # Resolve the same-build C ABI directly at load time. No weak or stale backend.
    s=replace(s,'\tmesa.handle = dlopen("/redwood/lib/libredwoodair.dylib", RTLD_NOW);',
                '\tmesa.handle = rwa_native_abi()==SWEET_NATIVE_METAL_ABI ? (void *)1 : NULL;')
    s,n=re.subn(r'\*\(void \*\*\)&mesa\.(\w+) = dlsym\(mesa.handle,\s*"(rwa_\w+)"\);',r'mesa.\1 = \2;',s)
    if n!=29:raise RuntimeError(f'incomplete symbol adaptation: {n}')
    s=s.replace('dlclose(mesa.handle);','/* backend is a required @loader_path dependency */')
    # Remove foreign kernel selectors: resource allocation must use this backend.
    begin=s.index('static bool\ngpu_param(');end=s.index('/* The kernel\'s gpu interface',begin)
    s=s[:begin]+'''static bool gpu_param(io_connect_t gpu, uint32_t which, uint64_t *value)
{
    (void)gpu;(void)which;(void)value;return false;
}

'''+s[end:]
    begin=s.index('\t/* The gpu this driver would actually drive.')
    end=s.index('\t/* The struct Metal reads',begin)
    s=s[:begin]+'''    _gpu=IO_OBJECT_NULL;
    char nativeWhy[512]={0};
    if(mesa_available())_mesa=mesa.device_open(nativeWhy,sizeof nativeWhy);
    if(!_mesa) {
        rw_print("native A618 backend unavailable: %s\\n",nativeWhy);
        [self release];return nil;
    }
    rw_print("native renderer %s; AIR -> NIR -> freedreno, no Vulkan\\n",mesa.device_name(_mesa));
    Dl_info nativeImage={0};
    if(dladdr((const void *)&rwa_render_draw,&nativeImage))
        rw_print("backend image %s base=%p\\n",nativeImage.dli_fname,nativeImage.dli_fbase);
    if(dladdr((const void *)&mesa_available,&nativeImage))
        rw_print("frontend image %s base=%p\\n",nativeImage.dli_fname,nativeImage.dli_fbase);

'''+s[end:]
    begin=s.index('\tif (gpu == IO_OBJECT_NULL || length == 0)',s.index('@implementation RedwoodMetalBuffer'))
    end=s.index('\n- (struct rwa_buffer *)mesaBuffer',begin)
    s=s[:begin]+'''    rw_print("native buffer allocation requires the AIR/freedreno backend\\n");
    [self release];return nil;
}
'''+s[end:]
    begin=s.index('\tif (_contents != NULL) {',s.index('@implementation RedwoodMetalBuffer'))
    end=s.index('\n\t[super dealloc];',begin)
    s=s[:begin]+s[end:]
    s=replace(s,'@property (readonly) struct rwa_buffer *mesaBuffer;', '@property (readonly) struct rwa_buffer *mesaBuffer;\n- (void *)contents;\n- (NSUInteger)length;')
    s=replace(s,'@interface RedwoodMetalCommandQueue : _MTLCommandQueue', '@interface RedwoodMetalCommandQueue : _MTLCommandQueue\n- (NSUInteger)maxCommandBufferCount;')
    s=replace(s,'\t    [[[MTLCommandQueueDescriptor alloc] init] autorelease];','''        [[[(NSClassFromString(@"MTLCommandQueueDescriptorInternal") ?: NSClassFromString(@"MTLCommandQueueDescriptor")) alloc] init] autorelease];
    if(!use){rw_print("Metal command queue descriptor unavailable\\n");return nil;}''')
    s=replace(s,'- (id)newCommandQueueWithDescriptor:(id)descriptor\n{','''- (id)newCommandQueue {
    return [self newCommandQueueWithDescriptor:nil];
}
- (id)newCommandQueueWithDescriptor:(id)descriptor
{''')
    # Increment and read were previously unsequenced in a logging macro.
    s=replace(s,'if ((++rw_times_ & (rw_times_ - 1)) == 0)', 'if ((rw_times_++, (rw_times_ & (rw_times_ - 1))) == 0)')
    # Real library and function objects retain exact specialization bytes.
    marker='@implementation RedwoodMetalDevice\n'
    s=replace(s,marker,'#include "SweetNativeLibrary.inc"\n\n'+marker)
    at=s.index('- (struct rwa_device *)mesaDevice',s.index(marker))
    s=s[:at]+'''- (id)newLibraryWithFile:(NSString *)path error:(NSError **)error {
    NSData *data=[NSData dataWithContentsOfFile:path options:0 error:error];
    return data?[[SweetNativeLibrary alloc] initWithDevice:self data:data error:error]:nil;
}
- (id)newLibraryWithURL:(NSURL *)url error:(NSError **)error {
    return [self newLibraryWithFile:[url path] error:error];
}
- (id)newLibraryWithData:(dispatch_data_t)data error:(NSError **)error {
    if(!data){if(error)*error=native_error("empty metallib data");return nil;}
    const void *bytes=NULL;size_t length=0;
    dispatch_data_t mapped=dispatch_data_create_map(data,&bytes,&length);
    NSData *copy=[NSData dataWithBytes:bytes length:length];
    id library=[[SweetNativeLibrary alloc] initWithDevice:self data:copy error:error];
    dispatch_release(mapped);return library;
}
- (id)newDefaultLibrary {
    NSString *path=[[NSBundle mainBundle] pathForResource:@"default" ofType:@"metallib"];
    return path?[self newLibraryWithFile:path error:NULL]:nil;
}
- (id)newDefaultLibraryWithBundle:(NSBundle *)bundle error:(NSError **)error {
    NSString *path=[bundle pathForResource:@"default" ofType:@"metallib"];
    if(!path){if(error)*error=native_error("bundle has no default.metallib");return nil;}
    return [self newLibraryWithFile:path error:error];
}
- (id)newLibraryWithSource:(NSString *)source options:(id)options error:(NSError **)error {
    (void)source;(void)options;if(error)*error=native_error("Metal source compilation is unavailable; supply AIR/metallib");return nil;
}
'''+s[at:]
    # The exact call sites can be found by the C-ABI calls, with the descriptor's
    # function objects still in scope. Never replace specialized values with zeros.
    at=s.index('\tstruct rwa_render *render = mesa.render_create(')
    s=s[:at]+'''    char nativeWhy[512]={0};
    if(!native_specialize(_mesa,vertex,0,nativeWhy,sizeof nativeWhy) ||
       !native_specialize(_mesa,fragment,1,nativeWhy,sizeof nativeWhy))
        return [self fail:error with:[NSString stringWithUTF8String:nativeWhy]];
'''+s[at:]
    # Inspect compute context for its actual function variable name.
    pattern=r'(\tstruct rwa_compute \*compute = mesa\.compute_create\()'
    match=re.search(pattern,s)
    if not match:raise RuntimeError('missing compute create')
    at=match.start()
    s=s[:at]+'''    char nativeWhy[512]={0};
    if(!native_specialize(_mesa,function,2,nativeWhy,sizeof nativeWhy))
        return [self fail:error with:[NSString stringWithUTF8String:nativeWhy]];
'''+s[at:]
    s=replace(s,'\tif (mesa.module_argbufs(_mesa, [air bytes], [air length],','''    if(!native_specialize(_mesa,function,2,why,sizeof why)){rw_print("argument encoder: %s\\n",why);return nil;}
	if (mesa.module_argbufs(_mesa, [air bytes], [air length],''')
    # Hold every referenced object through the actual command-buffer fence.
    s=replace(s,'@implementation RedwoodMetalWork\n@end','''@implementation RedwoodMetalWork
- (void)dealloc {
    [pipeline release];[render release];[depth release];[depthStencilState release];[indexBuffer release];
    for(unsigned i=0;i<8;i++)[color[i] release];
    for(unsigned i=0;i<RW_MAX_BUFFERS;i++){[vertexBuffers[i] release];[buffers[i] release];[textures[i] release];[samplers[i] release];}
    [super dealloc];
}
@end''')
    s=replace(s,'\t[_work addObject:work];','''    [work->pipeline retain];[work->render retain];[work->depth retain];[work->depthStencilState retain];[work->indexBuffer retain];
    for(unsigned i=0;i<8;i++)[work->color[i] retain];
    for(unsigned i=0;i<RW_MAX_BUFFERS;i++){[work->vertexBuffers[i] retain];[work->buffers[i] retain];[work->textures[i] retain];[work->samplers[i] retain];}
\t[_work addObject:work];''')
    # Every commit is one context transaction. This initial implementation waits
    # once at commit, including on partial failure, before completion callbacks.
    s=replace(s,'\t_status = RW_CB_COMMITTED;\n\tfor (RedwoodMetalWork *work in _work) {', '''    if(_status>=RW_CB_COMMITTED){[self failWith:"command buffer committed twice"];return;}
    _status = RW_CB_COMMITTED;
    if(rwa_command_begin([[_queue device] mesaDevice],why,sizeof why)) {
        [self failWith:why];[self redwoodReturnSlot];[self redwoodDeliverHandlers];return;
    }
\tfor (RedwoodMetalWork *work in _work) {''')
    s=replace(s,'done:\n\t/* The queue\'s slot goes back here', '''done:
    {
        char completionWhy[512]={0};
        // A failed encoding still waits its submitted prefix before reporting.
        if(_status!=RW_CB_ERROR && native_async_commits()) {
            uint64_t nativeFence=0;
            if(rwa_command_submit([[_queue device] mesaDevice],&nativeFence,completionWhy,sizeof completionWhy))
                [self failWith:completionWhy];
            else {
                // Stays scheduled; the completion thread completes it.
                _nativePending=YES;
                native_completion_enqueue(self,[[_queue device] mesaDevice],nativeFence);
            }
        } else if(rwa_command_end([[_queue device] mesaDevice],completionWhy,sizeof completionWhy))
            [self failWith:completionWhy];
        else if(_status!=RW_CB_ERROR)_status=RW_CB_COMPLETED;
    }
\t/* The queue's slot goes back here''')
    s=replace(s,'\tif (_status == RW_CB_ERROR) {\n\t\treturn;\n\t}',
                '\tif (_status == RW_CB_ERROR || _status == RW_CB_COMPLETED) {\n\t\treturn;\n\t}\n    atomic_fetch_add(&native_wait_until_completed,1);')
    # Asynchronous commits: completed handlers belong to the completion thread,
    # which delivers them after the fence and outside the object lock.
    s=replace(s,'\tNSMutableArray *_scheduled, *_completed;   /* handler blocks, copied to the heap */',
                '\tNSMutableArray *_scheduled, *_completed;   /* handler blocks, copied to the heap */\n\tBOOL _nativePending;   /* submitted asynchronously, fence not yet retired */')
    s=replace(s,'\tNSArray *completed = _completed;\n\n\t_scheduled = nil;\n\t_completed = nil;',
                '\tNSArray *completed = _nativePending ? nil : _completed;\n\n\t_scheduled = nil;\n\tif (!_nativePending)\n\t\t_completed = nil;')
    s=replace(s,'- (void)redwoodDeliverHandlers\n{','''- (NSArray *)nativeTakeCompleted:(int)rc why:(const char *)why queue:(dispatch_queue_t *)queue
{
    NSArray *completed=_completed;
    _completed=nil;_nativePending=NO;
    if(rc)[self failWith:why];
    else if(_status!=RW_CB_ERROR)_status=RW_CB_COMPLETED;
    *queue=[_queue redwoodCompletionQueue];
    if(*queue)dispatch_retain(*queue);
    return completed;
}

- (void)redwoodDeliverHandlers
{''')
    s=replace(s,'\tif (_status >= RW_CB_SCHEDULED) {\n\t\t[self redwoodDeliver:handler];\n\t\treturn;\n\t}\n\tif (_completed == nil)',
                '\tif (_status >= RW_CB_COMPLETED) {\n\t\t[self redwoodDeliver:handler];\n\t\treturn;\n\t}\n\tif (_completed == nil)')
    # Draw state is appended to the shared C ABI and recorded at encode time.
    s=replace(s,'\tBOOL hasDepthRange;','''\tBOOL hasDepthRange;
    float nativeViewport[4], nativeBlend[4];
    unsigned nativeScissor[4], nativeFlags;
    BOOL nativeClearOnly, nativeBarrier;
    unsigned nativeClearMask;
    float nativeClearColors[8][4];''')
    s=replace(s,'\tBOOL _hasDepthRange;','''\tBOOL _hasDepthRange;
    float _nativeViewport[4], _nativeBlend[4];
    unsigned _nativeScissor[4], _nativeFlags;
    unsigned _nativeClearMask;
    float _nativeClearColors[8][4];''')
    begin=s.index('- (void)setViewport:(MTLViewport)viewport\n{')
    end=s.index('/* The depth/stencil state,',begin)
    s=s[:begin]+'''- (void)setViewport:(MTLViewport)v {
    if(!isfinite(v.originX)||!isfinite(v.originY)||!isfinite(v.width)||!isfinite(v.height)||v.width<0||v.height<0||
       !isfinite(v.znear)||!isfinite(v.zfar)) {[_commandBuffer failWith:"invalid viewport"];return;}
    _nativeViewport[0]=v.originX;_nativeViewport[1]=v.originY;
    _nativeViewport[2]=v.width;_nativeViewport[3]=v.height;_nativeFlags|=1;
    _depthRange[0]=v.znear;_depthRange[1]=v.zfar;_hasDepthRange=YES;
}
- (void)setScissorRect:(MTLScissorRect)r {
    if(r.x>UINT32_MAX||r.y>UINT32_MAX||r.width>UINT32_MAX||r.height>UINT32_MAX){[_commandBuffer failWith:"scissor exceeds 32-bit range"];return;}
    _nativeScissor[0]=r.x;_nativeScissor[1]=r.y;_nativeScissor[2]=r.width;_nativeScissor[3]=r.height;_nativeFlags|=2;
}
- (void)setCullMode:(NSUInteger)mode {if(mode)[_commandBuffer failWith:"interface: culling is not implemented"];}
- (void)setFrontFacingWinding:(NSUInteger)mode {if(mode)[_commandBuffer failWith:"interface: non-default winding is not implemented"];}
- (void)setTriangleFillMode:(NSUInteger)mode {if(mode)[_commandBuffer failWith:"interface: wireframe is not implemented"];}

'''+s[end:]
    s=replace(s,'\t(void)r; (void)g; (void)b; (void)a;',
                '    _nativeBlend[0]=r;_nativeBlend[1]=g;_nativeBlend[2]=b;_nativeBlend[3]=a;_nativeFlags|=4;')
    s=replace(s,'\twork->hasDepthRange = _hasDepthRange;','''\twork->hasDepthRange = _hasDepthRange;
    memcpy(work->nativeViewport,_nativeViewport,sizeof _nativeViewport);
    memcpy(work->nativeScissor,_nativeScissor,sizeof _nativeScissor);
    memcpy(work->nativeBlend,_nativeBlend,sizeof _nativeBlend);
    work->nativeFlags=_nativeFlags;
    work->nativeClearMask=_nativeClearMask;
    memcpy(work->nativeClearColors,_nativeClearColors,sizeof _nativeClearColors);
    _nativeClearMask=0;_clears=NO;_clearsDepth=NO;_clearsStencil=NO;''')
    s=replace(s,'\t\t.fragment_buffer_table_count = work->count,','''\t\t.fragment_buffer_table_count = work->count,
        .native_flags=work->nativeFlags,
        .native_viewport={work->nativeViewport[0],work->nativeViewport[1],work->nativeViewport[2],work->nativeViewport[3]},
        .native_scissor={work->nativeScissor[0],work->nativeScissor[1],work->nativeScissor[2],work->nativeScissor[3]},
        .native_blend={work->nativeBlend[0],work->nativeBlend[1],work->nativeBlend[2],work->nativeBlend[3]},''')
    # Independent attachment clear values and exactly one clear per pass.
    s=replace(s,'\t\tif (i == 0 && [attachment loadAction] == MTLLoadActionClear) {',
                '\t\tif ([attachment loadAction] == MTLLoadActionClear) {')
    s=replace(s,'\t\t\t_clears = YES;', '''            _nativeClearMask|=1u<<i;
            _nativeClearColors[i][0]=c.red;_nativeClearColors[i][1]=c.green;
            _nativeClearColors[i][2]=c.blue;_nativeClearColors[i][3]=c.alpha;
\t\t\t_clears = YES;''')
    # Clear via Gallium before the first draw. EndEncoding records clear-only passes.
    s=replace(s,'\t\tif (work->render != nil) {','''        if(work->nativeBarrier) {
            if(rwa_barrier([[_queue device] mesaDevice],why,sizeof why)){[self failWith:why];goto done;}
            continue;
        }
        if(work->nativeClearMask || work->clearsDepth || work->clearsStencil) {
            struct rwa_texture *targets[8]={0};
            for(unsigned i=0;i<work->colorCount;i++)targets[i]=[work->color[i] mesaTexture];
            if(rwa_clear([[_queue device] mesaDevice],targets,work->colorCount,work->nativeClearMask,
                         work->nativeClearColors,[work->depth mesaTexture],
                         work->clearsDepth?&work->clearDepth:NULL,work->clearsStencil?&work->clearStencil:NULL,
                         why,sizeof why)){[self failWith:why];goto done;}
        }
        if(work->nativeClearOnly)continue;
\t\tif (work->render != nil) {''')
    s=replace(s,'\t\t.clear_color = work->clears ? work->clearColor : NULL,','\t\t.clear_color = NULL,')
    s=replace(s,'\t\t.clear_depth = work->clearsDepth ? &work->clearDepth : NULL,','\t\t.clear_depth = NULL,')
    s=replace(s,'\t\t.clear_stencil = work->clearsStencil ? &work->clearStencil : NULL,','\t\t.clear_stencil = NULL,')
    marker='- (void)endEncoding\n{\n\t[super endEncoding];\n}'
    # first is render encoder, second compute: only edit first.
    at=s.index(marker)
    s=s[:at]+s[at:].replace(marker,'''- (void)endEncoding
{
    if(_nativeClearMask || _clearsDepth || _clearsStencil) {
        RedwoodMetalWork *work=[RedwoodMetalWork new];work->nativeClearOnly=YES;
        work->colorCount=_colorCount;for(unsigned i=0;i<8;i++)work->color[i]=_color[i];
        work->nativeClearMask=_nativeClearMask;memcpy(work->nativeClearColors,_nativeClearColors,sizeof _nativeClearColors);
        work->depth=_depth;work->clearDepth=_clearDepth;work->clearStencil=_clearStencil;
        work->clearsDepth=_clearsDepth;work->clearsStencil=_clearsStencil;
        [_commandBuffer recordWork:work];[work release];
        _nativeClearMask=0;_clearsDepth=NO;_clearsStencil=NO;
    }
    [super endEncoding];
}''',1)
    at=s.index('- (void)recordWork:(RedwoodMetalWork *)work\n{')
    s=s[:at]+'''- (void)nativeRecordBarrier {
    RedwoodMetalWork *work=[RedwoodMetalWork new];work->nativeBarrier=YES;
    [self recordWork:work];[work release];
}
'''+s[at:]
    # Barriers became necessary once draws stopped waiting individually. Keep
    # their ordering in the recorded command stream, not at encoding time.
    edits=[]
    for m in re.finditer(r'(?m)^- \(void\)memoryBarrier',s):
        brace=s.index('{',m.start());end=s.index('}',brace)
        edits.append((brace+1,end,' [_commandBuffer nativeRecordBarrier]; '))
    for begin,end,body in reversed(edits):s=s[:begin]+body+s[end:]
    for operation in ('encodeSignalEvent','encodeWaitForEvent'):
        old='- (void)'+operation+':(id)event value:(uint64_t)value { (void)event; (void)value; }'
        new='- (void)'+operation+':(id)event value:(uint64_t)value { (void)value;if(event)[self failWith:"interface: shared events are not implemented"]; }'
        s=replace(s,old,new)
    # Errors encountered during encoding must survive commit.
    s=replace(s,'    if(_status>=RW_CB_COMMITTED){[self failWith:"command buffer committed twice"];return;}',
                '    if(_status==RW_CB_ERROR){[self redwoodReturnSlot];[self redwoodDeliverHandlers];return;}\n    if(_status>=RW_CB_COMMITTED){[self failWith:"command buffer committed twice"];return;}')
    s=replace(s,'- (id)iosurface                    { return nil; }',
                '- (id)iosurface                    { return (id)_nativeSurface; }')
    s=replace(s,'\tstruct rwa_texture_shape _shape;','\tstruct rwa_texture_shape _shape;\n    CFTypeRef _nativeSurface;')
    # keep exact IOSurface object for Metal's iosurface accessor as well as winsys ownership
    # Locate the creation/wrapping code in the donor and add via object_setIvar is
    # unnecessary: private ivars are available to a category setter below.
    begin=s.index('- (NSString *)name\n{',s.index('@implementation RedwoodMetalDevice'))
    end=s.index('\n/*',begin)
    s=s[:begin]+'''- (NSString *)name
{
    return @"Adreno 618 (Sweet Native Metal)";
}
'''+s[end:]
    s=replace(s,'@property (readonly) struct rwa_texture *mesaTexture;',
                '@property (readonly) struct rwa_texture *mesaTexture;\n- (void)nativeAttachSurface:(void *)surface;')
    begin=s.index('\treturn [[RedwoodMetalTexture alloc] initWithRedwoodDevice:self',s.index('- (id)newTextureWithDescriptor:(MTLTextureDescriptor *)d iosurface:'))
    end=s.index('\n}',begin)
    s=s[:begin]+'''    RedwoodMetalTexture *result=[[RedwoodMetalTexture alloc] initWithRedwoodDevice:self texture:texture shape:&shape];
    [result nativeAttachSurface:s];return result;'''+s[end:]
    at=s.index('+ (void)load',s.index('@implementation RedwoodMetalTexture'))
    s=s[:at]+'''- (void)nativeAttachSurface:(void *)surface {
    if(_nativeSurface)CFRelease(_nativeSurface);
    _nativeSurface=surface?CFRetain(surface):NULL;
}
'''+s[at:]
    begin=s.index('- (void)dealloc',s.index('@implementation RedwoodMetalTexture'))
    at=s.index('\n\t[super dealloc];',begin)
    s=s[:at]+'\n    if(_nativeSurface)CFRelease(_nativeSurface);'+s[at:]
    # Resources retain their owning device even when the application releases it.
    for cls in ('RedwoodMetalBuffer','RedwoodMetalTexture','RedwoodMetalComputePipeline','RedwoodMetalRenderPipeline','RedwoodMetalArgumentEncoder','RedwoodMetalCommandQueue'):
        begin=s.index('@implementation '+cls)
        end=s.index('\n@end',begin)
        part=s[begin:end]
        part=part.replace('_owner = device;','_owner = [device retain];')
        if '_owner = [device retain];' in part:
            part=part.replace('[super dealloc];','[_owner release];[super dealloc];')
        s=s[:begin]+part+s[end:]
    # Never globally replace methods on Apple's function class. Native functions
    # implement their own argument-encoder entrypoints.
    at=s.index('static void\nensure_function_encoder(void)\n{')
    s=s[:at]+'''static void
ensure_function_encoder(void)
{
    /* SweetNativeFunction owns this API; no process-wide Objective-C swizzle. */
}
'''
    s+=NATIVE_COMPLETION
    s=s.replace('RedwoodMetal','SweetNativeMetal')
    s=s.replace('redwoodmetal[','SweetNativeMetal[')
    # Draw log (frontend/SweetNativeDrawLog.inc): /var/local/sweet-native-drawlog.
    s=replace(s,'static struct {\n\tvoid *handle;','#include "SweetNativeDrawLog.inc"\n\nstatic struct {\n\tvoid *handle;')
    s=replace(s,'\treturn [[SweetNativeMetalRenderPipeline alloc] initWithRedwoodDevice:self\n',
        '\tnative_drawlog_remember(render, [[descriptor label] UTF8String],\n'
        '\t    vertex != nil ? [[vertex name] UTF8String] : "(generated)", [[fragment name] UTF8String], descriptor);\n'
        '\treturn [[SweetNativeMetalRenderPipeline alloc] initWithRedwoodDevice:self\n')
    s=replace(s,'\tif (mesa.render_draw([work->render render], &draw, why, size) != 0) {',
        '\t{\n'
        '\t\tconst unsigned dl = native_drawlog_next();\n'
        '\t\tif (dl)\n'
        '\t\t\tnative_drawlog_draw(dl, [work->render render], (unsigned)work->primitive,\n'
        '\t\t\t    (unsigned)work->vertexStart, (unsigned)work->vertexCount, work->indexBuffer != nil,\n'
        '\t\t\t    (unsigned)work->indexType, (size_t)work->indexBufferOffset, (unsigned)work->instanceCount,\n'
        '\t\t\t    work->color, work->colorCount, work->depth, work->depthStencilState != nil,\n'
        '\t\t\t    work->stencilReference[0], work->textures, work->textureCount);\n'
        '\t\tif (dl)\n'
        '\t\t\tnative_drawlog_detail(dl, [work->render render], work->colorCount ? work->color[0] : nil,\n'
        '\t\t\t    (unsigned)work->vertexStart, (unsigned)work->vertexCount, work->indexBuffer,\n'
        '\t\t\t    (unsigned)work->indexType, (size_t)work->indexBufferOffset, work->baseVertex,\n'
        '\t\t\t    work->vertexBuffers, work->vertexBufferOffsets, work->vertexBufferCount,\n'
        '\t\t\t    work->buffers, work->bufferOffsets, work->count);\n'
        '\t}\n'
        '\tif (mesa.render_draw([work->render render], &draw, why, size) != 0) {')
    p.write_text(s)
    shutil.copy2(NATIVE/'frontend/SweetNativeLibrary.inc',src/'SweetNativeLibrary.inc')
    shutil.copy2(NATIVE/'frontend/SweetNativeDrawLog.inc',src/'SweetNativeDrawLog.inc')
    shutil.copy2(NATIVE/'frontend/SweetNativeDrawable.m',src/'redwooddrawable.m')
    # The donor includes structs by hand in two files; only the drawable shim's
    # geometry record remains local. The AIR header itself is shared above.
    return p

def serialize_methods(path):
    s=path.read_text()
    support='''
// Gallium has one context per device. Serialize all object entrypoints, not just
// commits, because pipeline creation and CPU texture transfers also use it.
static pthread_mutex_t nativeObjectLock;
static pthread_once_t nativeLockOnce=PTHREAD_ONCE_INIT;
static void nativeLockInit(void){pthread_mutexattr_t a;pthread_mutexattr_init(&a);pthread_mutexattr_settype(&a,PTHREAD_MUTEX_RECURSIVE);pthread_mutex_init(&nativeObjectLock,&a);pthread_mutexattr_destroy(&a);}
static int nativeLock(void){pthread_once(&nativeLockOnce,nativeLockInit);pthread_mutex_lock(&nativeObjectLock);return 0;}
static void nativeUnlock(int *unused){(void)unused;pthread_mutex_unlock(&nativeObjectLock);}
#define NATIVE_OBJECT_LOCK __attribute__((cleanup(nativeUnlock))) int nativeGuard = nativeLock()
'''
    at=s.index('static struct {\n\tvoid *handle;')
    s=s[:at]+support+s[at:]
    # Only definitions, not @interface declarations. A method's next semicolon
    # appears before its opening brace for declarations.
    insert=[]
    for m in re.finditer(r'(?m)^[+-] \(',s):
        brace=s.find('{',m.start());semi=s.find(';',m.start())
        if brace>=0 and (semi<0 or brace<semi):
            signature=s[m.start():brace]
            # Apple's command-buffer constructor waits for a queue slot. Holding
            # this lock across that wait would prevent another thread's commit
            # from returning a slot. These factories do not access Gallium.
            if 'initWithRedwoodQueue:' in signature or re.match(r'- \(id\)commandBuffer(?:\s|WithUnretainedReferences)',signature):
                continue
            insert.append(brace+1)
    for at in reversed(insert):s=s[:at]+' NATIVE_OBJECT_LOCK; '+s[at:]
    path.write_text(s)
    p=path.parent/'SweetNativeLibrary.inc';s=p.read_text();insert=[]
    for m in re.finditer(r'(?m)^[+-] \(',s):
        brace=s.find('{',m.start());semi=s.find(';',m.start())
        if brace>=0 and (semi<0 or brace<semi):insert.append(brace+1)
    for at in reversed(insert):s=s[:at]+' NATIVE_OBJECT_LOCK; '+s[at:]
    p.write_text(s)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('donor',type=Path,help='disposable export of the pinned donor')
    args=parser.parse_args()
    adapt_backend(args.donor)
    frontend=adapt_frontend(args.donor)
    serialize_methods(frontend)
    print('Prepared native Sweet Metal sources:',args.donor)

if __name__=='__main__':main()
