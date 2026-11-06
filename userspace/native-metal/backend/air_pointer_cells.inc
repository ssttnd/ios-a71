/*
 * Sweet native Metal: pointer cells, constant globals and translation-time
 * program constants. Included into air_to_nir.c ahead of
 * translate_instruction by tools/prepare-sweet-native-metal.py.
 *
 * A pointer in this frontend is a resolution (a binding and a byte offset)
 * and not an address, so memory cannot hold one. QuartzCore's uber fragment
 * shaders (fixed_frag_lph_cph and fixed_frag_lph_cpf, iOS 16.7 20H392) still
 * keep their two sampler arguments in memory:
 *
 *   %37 = alloca ptr addrspace(2)                     ; entry block
 *   store ptr addrspace(2) %34, ptr %37               ; block 0, instruction 2
 *   ...
 *   %227 = bitcast ptr %37 to ptr                     ; another block
 *   %228 = select i1 %144, ptr @_ZL14unused_sampler, ptr %227
 *   %230 = load ptr addrspace(2), ptr (gep %228, 0, 0)
 *   ... @air.sample_texture_2d(..., ptr addrspace(2) %230, ...)
 *
 * where @_ZL14unused_sampler is `constant { ptr @__air_sampler_state }` and
 * %144 tests @_ZL15fc_has_image_0A, a program-scope variable the static
 * initializer copies from a function constant.
 *
 * A CELL is a translation-time memory location holding a resolution. Two
 * things become one, and nothing else:
 *
 *  - a local of pointer type stored exactly once, in the entry block, before
 *    any read of it, whose address is only cast, zero-offset GEP'd, selected
 *    or loaded from -- never stored, passed, compared or offset. The entry
 *    block runs once and first, so that store reaches every load, which is
 *    what mem2reg proves before it promotes a single-store alloca;
 *  - a constant global whose initializer is one pointer (possibly inside
 *    one-member wrappers such as metal::sampler). Constant memory is never
 *    written, so the initializer is its content.
 *
 * Everything else stays refused with its own reason.
 *
 * Constant globals with numeric initializers are laid out in
 * nir->constant_data (ir3 uploads it as a UBO) rather than becoming
 * uninitialized scratch, which is what the donor did with them.
 *
 * A program-scope variable written exactly once, unconditionally (block 0 of
 * a static initializer) and nowhere else, whose stored value folds to a
 * constant, reads as that constant outside the initializers. Together with
 * folding of pointer `select`s on constant conditions, this resolves the
 * sampler choice above at pipeline creation, as the function constants that
 * drive it intend.
 */

#include "nir_constant_expressions.h"
#include "util/set.h"

static const struct air_type *
cell_leaf_type(const struct air_type *t)
{
   while (t && (t->kind == AIR_TYPE_STRUCT || t->kind == AIR_TYPE_ARRAY)) {
      if (t->kind == AIR_TYPE_STRUCT) {
         if (t->num_members != 1)
            return NULL;
         t = t->members[0];
      } else {
         if (t->count != 1)
            return NULL;
         t = t->elem;
      }
   }
   return t && t->kind == AIR_TYPE_POINTER ? t : NULL;
}

static struct air_val *
air_val_cell(struct air_ctx *c, const struct air_type *type, const struct air_value *src)
{
   struct air_val *v = air_val_agg(c, 1);
   v->kind = AIR_VAL_PTR_CELL;
   v->ptr.pointee = type;
   v->cell_src = src;
   return v;
}

static bool
gep_indices_zero(const struct air_inst *in)
{
   for (unsigned i = 1; i < in->num_operands; i++) {
      uint64_t k;
      if (!air_const_uint(in->operands[i], &k) || k)
         return false;
   }
   return true;
}

static int
value_index(const struct air_value *const *set, unsigned n, const struct air_value *v)
{
   for (unsigned i = 0; i < n; i++)
      if (set[i] == v)
         return i;
   return -1;
}

static bool
pointer_cell_alloca(struct air_ctx *c, const struct air_inst *alloca)
{
   const struct air_function *f = c->fn;
   uint64_t count;
   if (!f || !f->num_blocks || alloca->parent != &f->blocks[0] ||
       !air_const_uint(alloca->operands[0], &count) || count != 1 ||
       !cell_leaf_type(alloca->aux_type))
      return false;

   /* The values that are this cell's address, grown to a fixed point. EXACT
    * ones name the cell alone; a select's result is a choice that may be read
    * through but never stored through.
    */
   unsigned cap = 8, n = 0;
   const struct air_value **set = ralloc_array(c->mem_ctx, const struct air_value *, cap);
   bool *exact = ralloc_array(c->mem_ctx, bool, cap);
   set[n] = &alloca->base;
   exact[n++] = true;
   const struct air_inst *store = NULL;

   for (bool grew = true; grew;) {
      grew = false;
      for (unsigned bi = 0; bi < f->num_blocks; bi++) {
         const struct air_block *b = &f->blocks[bi];
         for (unsigned ii = 0; ii < b->num_insts; ii++) {
            const struct air_inst *use = b->insts[ii];
            for (unsigned oi = 0; oi < use->num_operands; oi++) {
               int k = value_index(set, n, use->operands[oi]);
               if (k < 0)
                  continue;
               if (use->is_volatile)
                  return false;
               bool derived_exact = false;
               switch (use->op) {
               case AIR_OP_LOAD:
                  if (oi != 0 || use->base.type->kind != AIR_TYPE_POINTER)
                     return false;
                  continue;
               case AIR_OP_STORE:
                  /* operand 0 would store the cell's own address: an escape */
                  if (oi != 1 || !exact[k] ||
                      use->operands[0]->type->kind != AIR_TYPE_POINTER ||
                      (store && store != use))
                     return false;
                  store = use;
                  continue;
               case AIR_OP_CAST:
                  if (use->sub != AIR_CAST_BITCAST && use->sub != AIR_CAST_ADDRSPACECAST)
                     return false;
                  derived_exact = exact[k];
                  break;
               case AIR_OP_GEP:
                  if (oi != 0 || !gep_indices_zero(use))
                     return false;
                  derived_exact = exact[k];
                  break;
               case AIR_OP_SELECT:
                  if (oi == 0)
                     return false;
                  break;
               default:
                  /* call, phi, return, compare, ptrtoint, aggregate, atomic */
                  return false;
               }
               if (value_index(set, n, &use->base) >= 0)
                  continue;
               if (n == cap) {
                  cap *= 2;
                  set = reralloc(c->mem_ctx, set, const struct air_value *, cap);
                  exact = reralloc(c->mem_ctx, exact, bool, cap);
               }
               set[n] = &use->base;
               exact[n++] = derived_exact;
               grew = true;
            }
         }
      }
   }

   if (!store || store->parent != &f->blocks[0])
      return false;
   /* No read of the cell may come before its store in the entry block. A read
    * in any other block follows the whole entry block, store included.
    */
   const struct air_block *entry = &f->blocks[0];
   for (unsigned ii = 0; ii < entry->num_insts && entry->insts[ii] != store; ii++) {
      const struct air_inst *use = entry->insts[ii];
      if (use->op == AIR_OP_LOAD && value_index(set, n, use->operands[0]) >= 0)
         return false;
   }
   return true;
}

/* What a cell holds: the stored resolution, or a constant's initializer
 * resolved in the function being translated now (a global's resolution is
 * per function: materialise_globals).
 */
static struct air_val *
cell_content(struct air_ctx *c, struct air_val *cell)
{
   if (cell->cell_src) {
      struct air_val *v = air_get(c, cell->cell_src);
      if (v && !val_is_pointerish(v)) {
         air_error(c, "instruction: a constant pointer whose initializer is not a pointer");
         return NULL;
      }
      return v;
   }
   if (!cell->elems[0]) {
      air_error(c, "instruction: a pointer cell read before its store");
      return NULL;
   }
   return cell->elems[0];
}

/* A load of a POINTER: from a cell, from a choice of cells, or the argument
 * buffer resolution the donor already had. Never from bytes.
 */
static struct air_val *
load_pointer(struct air_ctx *c, struct air_val *p, const struct air_type *t)
{
   if (p->kind == AIR_VAL_PTR_CELL)
      return cell_content(c, p);
   if (p->kind == AIR_VAL_PTR_SELECT) {
      struct air_val *a = load_pointer(c, p->elems[0], t);
      struct air_val *b = a ? load_pointer(c, p->elems[1], t) : NULL;
      return b ? air_val_ptr_select(c, p->def, a, b) : NULL;
   }
   if (p->kind == AIR_VAL_PTR && p->ptr.class == AIR_PTR_ARGBUF)
      return argbuf_load(c, p->ptr, t);
   air_error(c, "instruction: a pointer loaded from memory that holds no resolution");
   return NULL;
}

/* A bitcast/addrspacecast of a pointer that is a cell or a choice: the cell
 * itself (one object, so a store through the cast is a store into it), or the
 * choice with each side retyped.
 */
static struct air_val *
retype_pointer(struct air_ctx *c, struct air_val *v, const struct air_type *pointee)
{
   if (v->kind == AIR_VAL_PTR_CELL)
      return v;
   if (v->kind == AIR_VAL_PTR) {
      struct air_ptr p = v->ptr;
      p.pointee = pointee;
      return air_val_ptr(c, p);
   }
   if (v->kind == AIR_VAL_PTR_SELECT) {
      struct air_val *a = retype_pointer(c, v->elems[0], pointee);
      struct air_val *b = a ? retype_pointer(c, v->elems[1], pointee) : NULL;
      return b ? air_val_ptr_select(c, v->def, a, b) : NULL;
   }
   return NULL;
}

/* -- translation-time constants ----------------------------------------- */

/* The value of DEF if it is computed from immediates alone, evaluated by
 * NIR's own constant expressions (what nir_opt_constant_folding would do
 * later). BUDGET bounds the walk over shared subexpressions.
 */
static bool
fold_def(struct air_ctx *c, nir_def *def, nir_const_value *out, unsigned *budget)
{
   nir_instr *instr = def->parent_instr;
   if (instr->type == nir_instr_type_load_const) {
      memcpy(out, nir_instr_as_load_const(instr)->value,
             def->num_components * sizeof(*out));
      return true;
   }
   if (instr->type != nir_instr_type_alu || !*budget)
      return false;
   (*budget)--;
   nir_alu_instr *alu = nir_instr_as_alu(instr);
   const nir_op_info *info = &nir_op_infos[alu->op];

   /* A choice on a known condition is the chosen side, whatever the other is:
    * `select(is_function_constant_defined(k), k, 0)` with k not supplied
    * selects 0 over an undef.
    */
   if (alu->op == nir_op_bcsel) {
      nir_const_value cond[NIR_MAX_VEC_COMPONENTS];
      if (!fold_def(c, alu->src[0].src.ssa, cond, budget))
         return false;
      bool first = nir_const_value_as_bool(cond[alu->src[0].swizzle[0]], 1);
      for (unsigned j = 1; j < alu->def.num_components; j++)
         if (nir_const_value_as_bool(cond[alu->src[0].swizzle[j]], 1) != first)
            return false;
      unsigned side = first ? 1 : 2;
      nir_const_value whole[NIR_MAX_VEC_COMPONENTS];
      if (!fold_def(c, alu->src[side].src.ssa, whole, budget))
         return false;
      for (unsigned j = 0; j < alu->def.num_components; j++)
         out[j] = whole[alu->src[side].swizzle[j]];
      return true;
   }
   nir_const_value src[NIR_ALU_MAX_INPUTS][NIR_MAX_VEC_COMPONENTS];
   nir_const_value *srcs[NIR_ALU_MAX_INPUTS];
   unsigned bit_size = 0;
   if (!nir_alu_type_get_type_size(info->output_type))
      bit_size = alu->def.bit_size;
   for (unsigned i = 0; i < info->num_inputs; i++) {
      nir_def *s = alu->src[i].src.ssa;
      if (!bit_size && !nir_alu_type_get_type_size(info->input_types[i]))
         bit_size = s->bit_size;
      nir_const_value whole[NIR_MAX_VEC_COMPONENTS];
      if (!fold_def(c, s, whole, budget))
         return false;
      for (unsigned j = 0; j < nir_ssa_alu_instr_src_components(alu, i); j++)
         src[i][j] = whole[alu->src[i].swizzle[j]];
      srcs[i] = src[i];
   }
   if (!bit_size)
      bit_size = 32;
   memset(out, 0, alu->def.num_components * sizeof(*out));
   nir_eval_const_opcode(alu->op, out, alu->def.num_components, bit_size, srcs,
                         c->nir->info.float_controls_execution_mode);
   return true;
}

static bool
fold_scalar(struct air_ctx *c, nir_def *def, uint64_t *out)
{
   nir_const_value v[NIR_MAX_VEC_COMPONENTS];
   unsigned budget = 256;
   if (def->num_components != 1 || !fold_def(c, def, v, &budget))
      return false;
   *out = nir_const_value_as_uint(v[0], def->bit_size);
   return true;
}

static bool
function_is_static_init(const struct air_function *f)
{
   return !f->is_declaration && f->section && !strcmp(f->section, "air.static_init");
}

static bool
inst_in_static_init(const struct air_ctx *c, const struct air_inst *in)
{
   for (unsigned i = 0; i < c->m->num_functions; i++) {
      const struct air_function *f = c->m->functions[i];
      if (function_is_static_init(f) && f->num_blocks &&
          in->parent >= &f->blocks[0] && in->parent < &f->blocks[f->num_blocks])
         return true;
   }
   return false;
}

static int
global_index(const struct air_ctx *c, const struct air_value *v)
{
   if (!v || v->kind != AIR_VALUE_GLOBAL)
      return -1;
   for (unsigned i = 0; i < c->m->num_globals; i++)
      if (&c->m->globals[i]->base == v)
         return i;
   return -1;
}

/* Every global named inside constant V other than as the base of an address
 * (an aggregate holding its address, a ptrtoint of it) loses its shadow.
 */
static void
taint_globals_in(struct air_ctx *c, const struct air_value *v, bool *tainted,
                 struct set *seen, unsigned depth)
{
   if (!v || depth > 64)
      return;
   int g = global_index(c, v);
   if (g >= 0) {
      tainted[g] = true;
      return;
   }
   const struct air_constant *k = air_value_as_constant(v);
   if (!k || _mesa_set_search(seen, k))
      return;
   _mesa_set_add(seen, k);
   for (unsigned i = 0; k->ckind == AIR_CONST_AGGREGATE && i < k->num_elems; i++)
      taint_globals_in(c, k->elems[i], tainted, seen, depth + 1);
   for (unsigned i = 0; k->ckind == AIR_CONST_EXPR && i < k->num_operands; i++)
      taint_globals_in(c, k->operands[i], tainted, seen, depth + 1);
}

/* The global a constant ADDRESS names -- the global itself, or a constant
 * getelementptr/bitcast/addrspacecast on it -- or -1. */
static int
const_address_global(const struct air_ctx *c, const struct air_value *v)
{
   for (unsigned depth = 0; v && depth < 16; depth++) {
      int g = global_index(c, v);
      if (g >= 0)
         return g;
      const struct air_constant *k = air_value_as_constant(v);
      if (!k || k->ckind != AIR_CONST_EXPR || !k->num_operands)
         return -1;
      if (k->expr_op != AIR_OP_GEP &&
          !(k->expr_op == AIR_OP_CAST && (k->expr_sub == AIR_CAST_BITCAST ||
                                          k->expr_sub == AIR_CAST_ADDRSPACECAST)))
         return -1;
      v = k->operands[0];
   }
   return -1;
}

/* Operand OI of IN carries an address into its result (which then addresses
 * the same global): a getelementptr base, a pointer cast, a side of a
 * select, a phi's incoming value. */
static bool
derives_address(const struct air_inst *in, unsigned oi)
{
   switch (in->op) {
   case AIR_OP_GEP:
      return oi == 0;
   case AIR_OP_CAST:
      return oi == 0 && (in->sub == AIR_CAST_BITCAST || in->sub == AIR_CAST_ADDRSPACECAST);
   case AIR_OP_SELECT:
      return oi == 1 || oi == 2;
   case AIR_OP_PHI:
      return true;
   default:
      return false;
   }
}

/* Which global VALUE addresses: -1 none, -2 more than one (already tainted). */
static int
address_origin(const struct air_ctx *c, struct hash_table *from, const struct air_value *v)
{
   int g = const_address_global(c, v);
   if (g >= 0)
      return g;
   struct hash_entry *e = _mesa_hash_table_search(from, v);
   return e ? (int)(intptr_t)e->data - 2 : -1;
}

static bool
inst_in_static_init_entry(const struct air_ctx *c, const struct air_inst *in)
{
   for (unsigned i = 0; i < c->m->num_functions; i++) {
      const struct air_function *f = c->m->functions[i];
      if (function_is_static_init(f) && f->num_blocks && in->parent == &f->blocks[0])
         return true;
   }
   return false;
}

static bool lay_out_constant(struct air_ctx *c, const struct air_type *t,
                             const struct air_value *v, uint8_t *dst, uint64_t room);

/* Program-scope memory, known byte by byte at translation time.
 *
 * A program-scope variable whose address is only ever loaded through, and
 * stored through only in a static initializer -- which runs once, before the
 * body -- holds at every load in the body its initializer overwritten, in
 * program order, by those stores. A store in a block the initializer
 * certainly runs exactly once (edges.certain) writes its bytes; one anywhere
 * else may or may not happen, and makes them unknown. Every address
 * derived from it (constant or instruction getelementptr, casts, selects,
 * phis) is followed; an address stored, passed to a call, compared, or used
 * for a store anywhere else excludes the variable. The shadow starts as the
 * laid-out initializer; a store whose value folds writes its bytes, any other
 * store makes them unknown.
 */
static void
plan_static_constants(struct air_ctx *c)
{
   unsigned ng = c->m->num_globals;
   if (!ng)
      return;
   bool *tainted = rzalloc_array(c->mem_ctx, bool, ng);
   struct set *seen = _mesa_pointer_set_create(c->mem_ctx);

   for (unsigned i = 0; i < ng; i++) {
      const struct air_global *g = c->m->globals[i];
      const char *name = g->base.name ? g->base.name : "";
      if (strncmp(name, "llvm.", 5))
         taint_globals_in(c, g->init, tainted, seen, 0);
   }
   for (unsigned fi = 0; fi < c->m->num_functions; fi++) {
      const struct air_function *f = c->m->functions[fi];
      if (f->is_declaration)
         continue;
      bool init = function_is_static_init(f);
      /* value -> origin + 2 */
      struct hash_table *from = _mesa_pointer_hash_table_create(c->mem_ctx);
      for (bool grew = true; grew;) {
         grew = false;
         for (unsigned bi = 0; bi < f->num_blocks; bi++) {
            const struct air_block *b = &f->blocks[bi];
            for (unsigned ii = 0; ii < b->num_insts; ii++) {
               const struct air_inst *in = b->insts[ii];
               int origin = -1;
               for (unsigned oi = 0; oi < in->num_operands; oi++) {
                  if (!derives_address(in, oi))
                     continue;
                  int g = address_origin(c, from, in->operands[oi]);
                  if (g == -1)
                     continue;
                  if (origin == -1) {
                     origin = g;
                  } else if (origin != g) {
                     if (origin >= 0)
                        tainted[origin] = true;
                     if (g >= 0)
                        tainted[g] = true;
                     origin = -2;
                  }
               }
               if (origin == -1)
                  continue;
               /* -1 -> one global -> -2 (several): monotone, so this ends */
               struct hash_entry *e = _mesa_hash_table_search(from, &in->base);
               int had = e ? (int)(intptr_t)e->data - 2 : -1;
               if (had == origin || had == -2) {
                  if (had == -2 && origin >= 0)
                     tainted[origin] = true;
                  continue;
               }
               if (had >= 0) {
                  tainted[had] = true;
                  if (origin >= 0)
                     tainted[origin] = true;
                  origin = -2;
               }
               _mesa_hash_table_insert(from, &in->base, (void *)(intptr_t)(origin + 2));
               grew = true;
            }
         }
      }
      for (unsigned bi = 0; bi < f->num_blocks; bi++) {
         const struct air_block *b = &f->blocks[bi];
         for (unsigned ii = 0; ii < b->num_insts; ii++) {
            const struct air_inst *in = b->insts[ii];
            for (unsigned oi = 0; oi < in->num_operands; oi++) {
               const struct air_value *v = in->operands[oi];
               int g = address_origin(c, from, v);
               if (g == -1) {
                  if (v && v->kind == AIR_VALUE_CONSTANT)
                     taint_globals_in(c, v, tainted, seen, 0);
                  continue;
               }
               if (g < 0 || derives_address(in, oi))
                  continue;
               if (in->is_volatile)
                  tainted[g] = true;
               else if (in->op == AIR_OP_LOAD && oi == 0)
                  ;
               else if (in->op == AIR_OP_STORE && oi == 1 && init)
                  ;   /* shadow_store decides: a block certainly run once, or unknown */
               else
                  tainted[g] = true;
            }
         }
      }
   }
   for (unsigned i = 0; i < ng; i++) {
      struct air_global_plan *pl = &c->global_plan[i];
      const struct air_global *g = c->m->globals[i];
      if (pl->skip || pl->class != AIR_PTR_SCRATCH || tainted[i] || !g->init)
         continue;
      uint64_t size = air_type_alloc_size(c->m, g->value_type);
      if (!size || size > 4096)
         continue;
      pl->shadow = rzalloc_size(c->mem_ctx, size);
      pl->shadow_known = rzalloc_size(c->mem_ctx, size);
      pl->shadow_size = size;
      if (lay_out_constant(c, g->value_type, g->init, pl->shadow, size))
         memset(pl->shadow_known, 1, size);
   }
}

/* the shadowed variable holding scratch byte OFF, or NULL */
static struct air_global_plan *
shadow_at(struct air_ctx *c, uint64_t off)
{
   for (unsigned i = 0; i < c->m->num_globals; i++) {
      struct air_global_plan *pl = &c->global_plan[i];
      if (pl->shadow && off >= pl->offset && off < pl->offset + pl->shadow_size)
         return pl;
   }
   return NULL;
}

static bool store_certain(const struct air_ctx *c, const struct air_inst *in);

/* A store translated in a static initializer. */
static void
shadow_store(struct air_ctx *c, const struct air_inst *in, struct air_ptr ptr, nir_def *value)
{
   uint64_t off = 0;
   if (ptr.class != AIR_PTR_SCRATCH || !inst_in_static_init(c, in))
      return;
   /* getelementptr builds array steps as imul(u2u32(i), stride): fold them */
   if (ptr.offset && !fold_scalar(c, ptr.offset, &off)) {
      /* which bytes is not known here: none of them are any more */
      for (unsigned i = 0; i < c->m->num_globals; i++)
         if (c->global_plan[i].shadow)
            memset(c->global_plan[i].shadow_known, 0, c->global_plan[i].shadow_size);
      return;
   }
   unsigned elem = value->bit_size == 1 ? 1 : value->bit_size / 8;
   unsigned bytes = elem * value->num_components;
   nir_const_value v[NIR_MAX_VEC_COMPONENTS];
   unsigned budget = 256;
   bool known = store_certain(c, in) && value->num_components <= NIR_MAX_VEC_COMPONENTS &&
                fold_def(c, value, v, &budget);
   for (unsigned b = 0; b < bytes; b++) {
      struct air_global_plan *pl = shadow_at(c, off + b);
      if (!pl)
         continue;
      unsigned at = off + b - pl->offset;
      pl->shadow_known[at] = known;
      if (known) {
         uint64_t word = nir_const_value_as_uint(v[b / elem], value->bit_size);
         pl->shadow[at] = word >> (8 * (b % elem));
      }
   }
}

/* A load, outside the initializers, of program-scope bytes all known. */
static struct air_val *
shadow_load(struct air_ctx *c, const struct air_inst *in, struct air_ptr ptr)
{
   const struct air_type *t = in->base.type;
   uint64_t off = 0;
   if (ptr.class != AIR_PTR_SCRATCH || in->is_volatile || t->kind == AIR_TYPE_POINTER ||
       t->kind == AIR_TYPE_STRUCT || t->kind == AIR_TYPE_ARRAY ||
       (ptr.offset && !fold_scalar(c, ptr.offset, &off)) ||
       c->in_static_init || inst_in_static_init(c, in))
      return NULL;
   unsigned comps = air_nir_components(t), bits = air_nir_bit_size(c, t);
   unsigned elem = bits == 1 ? 1 : bits / 8;
   if (!elem || comps > NIR_MAX_VEC_COMPONENTS || bits > 64)
      return NULL;
   struct air_global_plan *pl = shadow_at(c, off);
   if (!pl || off + elem * comps > pl->offset + pl->shadow_size)
      return NULL;
   nir_const_value v[NIR_MAX_VEC_COMPONENTS];
   memset(v, 0, sizeof(v));
   for (unsigned i = 0; i < comps; i++) {
      uint64_t word = 0;
      for (unsigned b = 0; b < elem; b++) {
         unsigned at = off - pl->offset + i * elem + b;
         if (!pl->shadow_known[at])
            return NULL;
         word |= (uint64_t)pl->shadow[at] << (8 * b);
      }
      v[i] = nir_const_value_for_uint(bits == 1 ? word != 0 : word, bits);
   }
   return air_val_ssa(c, nir_build_imm(&c->b, comps, bits, v));
}

/* Program-scope variables start as their initializers. The donor left them
 * as uninitialized scratch; a load the shadow cannot answer reads these.
 */
static void
store_initializers(struct air_ctx *c)
{
   for (unsigned i = 0; i < c->m->num_globals; i++) {
      const struct air_global_plan *pl = &c->global_plan[i];
      const struct air_global *g = c->m->globals[i];
      const struct air_constant *k = air_value_as_constant(g->init);
      if (pl->skip || pl->class != AIR_PTR_SCRATCH || !pl->has_offset || !k ||
          k->ckind == AIR_CONST_UNDEF || k->ckind == AIR_CONST_POISON)
         continue;
      uint64_t size = air_type_alloc_size(c->m, g->value_type);
      if (!size || size > 4096)
         continue;
      uint8_t *bytes = rzalloc_size(c->mem_ctx, size);
      if (!lay_out_constant(c, g->value_type, g->init, bytes, size)) {
         air_note(c, "a program-scope variable whose initializer is not plain data "
                     "starts uninitialized");
         continue;
      }
      for (uint64_t b = 0; b < size;) {
         unsigned n = (pl->offset + b) % 4 == 0 && size - b >= 4 ? 4 : 1;
         uint32_t word = 0;
         for (unsigned j = 0; j < n; j++)
            word |= (uint32_t)bytes[b + j] << (8 * j);
         nir_store_scratch(&c->b, nir_imm_intN_t(&c->b, word, 8 * n),
                           nir_imm_int(&c->b, pl->offset + b), .align_mul = n,
                           .write_mask = 1);
         b += n;
      }
   }
}

/* Scratch nothing reads is dead: after the shadows answer every load of the
 * function-constant state, the initializers' stores need not run per pixel.
 */
static void
drop_unread_scratch(nir_shader *nir)
{
   nir_foreach_function_impl(impl, nir) {
      nir_foreach_block(block, impl) {
         nir_foreach_instr(instr, block) {
            if (instr->type == nir_instr_type_intrinsic &&
                nir_instr_as_intrinsic(instr)->intrinsic == nir_intrinsic_load_scratch)
               return;
         }
      }
   }
   nir_foreach_function_impl(impl, nir) {
      nir_foreach_block(block, impl) {
         nir_foreach_instr_safe(instr, block) {
            if (instr->type == nir_instr_type_intrinsic &&
                nir_instr_as_intrinsic(instr)->intrinsic == nir_intrinsic_store_scratch)
               nir_instr_remove(instr);
         }
      }
      nir_metadata_preserve(impl, nir_metadata_control_flow);
   }
   nir->scratch_size = 0;
}

/* -- constant globals --------------------------------------------------- */

static void
put_bytes(uint8_t *dst, uint64_t v, unsigned n)
{
   for (unsigned i = 0; i < n; i++)
      dst[i] = v >> (8 * i);
}

/* The bytes of constant V of type T, at DST (ROOM bytes, zeroed). */
static bool
lay_out_constant(struct air_ctx *c, const struct air_type *t, const struct air_value *v,
                 uint8_t *dst, uint64_t room)
{
   uint64_t size = air_type_alloc_size(c->m, t);
   const struct air_constant *k = air_value_as_constant(v);
   if (!k || size > room || t->kind == AIR_TYPE_POINTER)
      return false;
   switch (k->ckind) {
   case AIR_CONST_NULL:
   case AIR_CONST_UNDEF:
   case AIR_CONST_POISON:
      return true;   /* zero bytes; any value refines undef */
   case AIR_CONST_INT:
   case AIR_CONST_FLOAT: {
      unsigned bytes = DIV_ROUND_UP(air_type_bit_size(t), 8);
      if (t->kind == AIR_TYPE_VECTOR || t->kind == AIR_TYPE_STRUCT ||
          t->kind == AIR_TYPE_ARRAY || bytes > 8 || bytes > size)
         return false;
      put_bytes(dst, k->u, bytes);
      return true;
   }
   case AIR_CONST_DATA:
   case AIR_CONST_AGGREGATE:
      for (unsigned i = 0; i < k->num_elems; i++) {
         const struct air_type *et;
         uint64_t off;
         if (t->kind == AIR_TYPE_STRUCT) {
            if (i >= t->num_members)
               return false;
            et = t->members[i];
            off = air_struct_member_offset(c->m, t, i);
         } else if (t->kind == AIR_TYPE_ARRAY) {
            if (i >= t->count)
               return false;
            et = t->elem;
            off = i * air_type_alloc_size(c->m, et);
         } else if (t->kind == AIR_TYPE_VECTOR) {
            /* <N x i1> is bit-packed in memory; nothing here needs it */
            if (i >= t->count || air_type_bit_size(t->elem) < 8)
               return false;
            et = t->elem;
            off = i * (air_type_bit_size(et) / 8);
         } else {
            return false;
         }
         if (off > size)
            return false;
         if (k->ckind == AIR_CONST_DATA) {
            unsigned bytes = DIV_ROUND_UP(air_type_bit_size(et), 8);
            if (et->kind == AIR_TYPE_POINTER || et->kind == AIR_TYPE_STRUCT ||
                et->kind == AIR_TYPE_ARRAY || et->kind == AIR_TYPE_VECTOR ||
                bytes > 8 || off + bytes > size)
               return false;
            put_bytes(dst + off, k->data[i], bytes);
         } else if (!lay_out_constant(c, et, k->elems[i], dst + off, size - off)) {
            return false;
         }
      }
      return true;
   default:
      return false;   /* a constant expression: an address, not bytes */
   }
}

/* A constant global with an initializer. True when planned here; false keeps
 * the donor's placement, whose loads are then reached only as before.
 */
static bool
plan_constant_global(struct air_ctx *c, struct air_global_plan *pl,
                     const struct air_global *g)
{
   if (cell_leaf_type(g->value_type)) {
      const struct air_value *leaf = g->init;
      /* through the one-member wrappers, and the pointer bitcasts typed
       * bitcode puts around the target (i64 addrspace(2)* @__air_sampler_state
       * as %struct._sampler_t addrspace(2)*), which change no resolution
       */
      while (leaf && leaf->kind == AIR_VALUE_CONSTANT) {
         const struct air_constant *k = air_value_as_constant(leaf);
         if (k->ckind == AIR_CONST_AGGREGATE && k->num_elems == 1)
            leaf = k->elems[0];
         else if (k->ckind == AIR_CONST_EXPR && k->expr_op == AIR_OP_CAST &&
                  k->expr_sub == AIR_CAST_BITCAST && k->num_operands == 1)
            leaf = k->operands[0];
         else
            break;
      }
      if (!leaf || leaf->kind != AIR_VALUE_GLOBAL)
         return false;
      pl->cell = true;
      pl->cell_leaf = leaf;
      return true;
   }

   uint64_t size = air_type_alloc_size(c->m, g->value_type);
   if (!size || size > 65536)
      return false;
   uint8_t *bytes = rzalloc_size(c->mem_ctx, size);
   if (!lay_out_constant(c, g->value_type, g->init, bytes, size)) {
      air_note(c, "a constant global whose initializer is not plain data keeps "
                  "uninitialized scratch placement");
      return false;
   }
   unsigned align = MAX2(MAX2(g->align, air_type_abi_align(c->m, g->value_type)), 4);
   unsigned off = ALIGN_POT(c->nir->constant_data_size, align);
   c->nir->constant_data = rerzalloc_size(c->nir, c->nir->constant_data,
                                          c->nir->constant_data_size, off + size);
   memcpy((uint8_t *)c->nir->constant_data + off, bytes, size);
   c->nir->constant_data_size = off + size;
   pl->class = AIR_PTR_CONSTANT_DATA;
   pl->offset = off;
   pl->has_offset = true;
   return true;
}

/* -- executable edges --------------------------------------------------- */

/* Which CFG edges control can take once function constants are known.
 *
 * Metal specializes a function by folding its constants and deleting the code
 * they make dead; NIR's later folding does the same for numbers, but a
 * pointer -- a texture, a sampler, a buffer -- is decided while translating.
 * The uber shaders choose `has_image ? argument : get_null_texture()` with a
 * branch and a phi, so the phi can only be resolved once the branch is.
 *
 * A conditional br or a switch whose condition folds emits only its taken
 * edge, and a block with no live incoming edge is not translated. Blocks are
 * visited in reverse post order, so only a back edge can still be undecided
 * when its target is reached; in a reducible CFG the header dominates the
 * back edge's source, so a header no forward edge enters is dead with its
 * whole loop. An irreducible region that breaks this is caught afterwards
 * (edges_check) and refused. For a phi of pointers an undecided edge counts
 * as live, which keeps resolve_pointer_phi's refusal of loop-carried pointers.
 */
struct air_edges {
   const struct air_function *fn;
   const bool *reachable;     /* statically, from the entry */
   bool *processed;           /* its terminator has been decided */
   bool *live;                /* some live edge enters it */
   bool *skipped;             /* reached in order with no live edge into it */
   /* certainly run exactly once, for shadow_store: the entry, or a block
    * entered by one live edge, from a certain block that took only it, with
    * no edge still undecided (a back edge) */
   bool *certain;
   unsigned *live_in, *certain_in, *taken_count, *last_taken;
   struct set *taken;         /* edges, as edge_key */
};

static void *
edge_key(unsigned from, unsigned to)
{
   return (void *)(uintptr_t)((((uint64_t)from << 32) | to) + 1);
}

static void
edge_take(struct air_edges *e, unsigned from, const struct air_block *to)
{
   if (!_mesa_set_search(e->taken, edge_key(from, to->index))) {
      e->live_in[to->index]++;
      e->taken_count[from]++;
      e->last_taken[from] = to->index;
   }
   _mesa_set_add(e->taken, edge_key(from, to->index));
   e->live[to->index] = true;
}

/* After block FROM's terminator: does it hand certainty on? */
static void
edges_decided(struct air_edges *e, unsigned from)
{
   e->processed[from] = true;
   if (e->certain[from] && e->taken_count[from] == 1)
      e->certain_in[e->last_taken[from]]++;
}

static bool
undecided_edge_into(const struct air_edges *e, unsigned bi)
{
   for (unsigned p = 0; p < e->fn->num_blocks; p++) {
      const struct air_block *pb = &e->fn->blocks[p];
      if (!pb->num_insts || !e->reachable[p] || e->processed[p])
         continue;
      const struct air_inst *term = pb->insts[pb->num_insts - 1];
      for (unsigned k = 0; k < term->num_targets; k++)
         if (term->targets[k] && term->targets[k]->index == bi)
            return true;
   }
   return false;
}

static bool
edge_possible(const struct air_edges *e, unsigned from, unsigned to)
{
   if (!e->reachable[from])
      return false;
   if (!e->processed[from])
      return true;
   return _mesa_set_search(e->taken, edge_key(from, to)) != NULL;
}

static bool
block_executable(struct air_edges *e, unsigned bi)
{
   if (bi == 0 || e->live[bi]) {
      /* only a static initializer's stores ask, so only it pays for the scan */
      e->certain[bi] = bi == 0 ||
                       (function_is_static_init(e->fn) && e->live_in[bi] == 1 &&
                        e->certain_in[bi] == 1 && !undecided_edge_into(e, bi));
      return true;
   }
   e->skipped[bi] = true;
   return false;
}

/* A block skipped as dead that a live edge enters after all: control flow
 * this ordering cannot decide. Refused, never translated without the block. */
static bool
edges_check(struct air_ctx *c, const struct air_edges *e)
{
   for (unsigned bi = 0; bi < e->fn->num_blocks; bi++)
      if (e->skipped[bi] && e->live[bi])
         return air_error(c, "instruction: block %u entered by a live back edge after it "
                             "was found dead (irreducible control flow)", bi);
   return true;
}

static void
edges_init(struct air_ctx *c, struct air_edges *e, const struct air_function *f,
           const bool *reachable)
{
   unsigned n = MAX2(f->num_blocks, 1);
   e->fn = f;
   e->reachable = reachable;
   e->processed = rzalloc_array(c->mem_ctx, bool, n);
   e->live = rzalloc_array(c->mem_ctx, bool, n);
   e->skipped = rzalloc_array(c->mem_ctx, bool, n);
   e->certain = rzalloc_array(c->mem_ctx, bool, n);
   e->live_in = rzalloc_array(c->mem_ctx, unsigned, n);
   e->certain_in = rzalloc_array(c->mem_ctx, unsigned, n);
   e->taken_count = rzalloc_array(c->mem_ctx, unsigned, n);
   e->last_taken = rzalloc_array(c->mem_ctx, unsigned, n);
   e->taken = _mesa_pointer_set_create(c->mem_ctx);
}

/* The case a switch takes on a known condition: its target, default included. */
static const struct air_block *
switch_known_target(const struct air_inst *term, uint64_t value, unsigned bits)
{
   uint64_t mask = bits >= 64 ? ~0ull : ((1ull << bits) - 1);
   for (unsigned k = 0; k + 1 < term->num_targets; k++) {
      uint64_t v;
      if (!air_const_uint(term->operands[1 + k], &v))
         return NULL;
      if ((v & mask) == (value & mask))
         return term->targets[1 + k];
   }
   return term->targets[0];
}

/* Is IN, a store in a static initializer, certainly run exactly once? Block 0
 * always is -- also when a one-block initializer is translated in place,
 * outside translate_body. */
static bool
store_certain(const struct air_ctx *c, const struct air_inst *in)
{
   if (inst_in_static_init_entry(c, in))
      return true;
   const struct air_edges *e = c->edges;
   return e && in->parent >= &e->fn->blocks[0] &&
          in->parent < &e->fn->blocks[e->fn->num_blocks] && e->certain[in->parent->index];
}

/* The one value a phi can take when every incoming edge but those carrying
 * that value is ruled out, and those were decided: the phi IS that value.
 * NULL when two values can still arrive, or an edge (a back edge) is not
 * decided yet. Lets a value the function constants fix flow through the
 * joins of the static initializer into the shadow, instead of a register.
 */
static const struct air_value *
single_incoming(const struct air_edges *e, const struct air_inst *phi)
{
   const struct air_value *v = NULL;
   for (unsigned k = 0; k < phi->num_operands; k++) {
      if (!phi->targets[k] ||
          !edge_possible(e, phi->targets[k]->index, phi->parent->index))
         continue;
      if (!e->processed[phi->targets[k]->index] || (v && v != phi->operands[k]))
         return NULL;
      v = phi->operands[k];
   }
   return v;
}
