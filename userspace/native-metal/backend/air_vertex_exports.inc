/* SPDX-License-Identifier: MIT
 * A6xx VPC output linkage has an 8-bit register ID and no half-register bit.
 * ir3_link_shaders forwards outputs[].regid without outputs[].half. Metal AIR
 * can explicitly declare half vertex outputs; export their numeric value from
 * the full register file instead of selecting unrelated full-register contents.
 */
static unsigned
native_widen_vertex_exports(nir_shader *s)
{
   if (s->info.stage != MESA_SHADER_VERTEX)
      return 0;
   nir_builder b = nir_builder_at(nir_before_impl(nir_shader_get_entrypoint(s)));
   unsigned widened = 0;
   nir_foreach_function_impl(impl, s) {
      nir_foreach_block(block, impl) {
         nir_foreach_instr_safe(instr, block) {
            if (instr->type != nir_instr_type_intrinsic)
               continue;
            nir_intrinsic_instr *in = nir_instr_as_intrinsic(instr);
            if (in->intrinsic != nir_intrinsic_store_output || in->src[0].ssa->bit_size != 16)
               continue;
            b.cursor = nir_before_instr(instr);
            nir_alu_type type = nir_intrinsic_src_type(in);
            nir_def *v;
            switch (type) {
            case nir_type_float16: v=nir_f2f32(&b,in->src[0].ssa);type=nir_type_float32;break;
            case nir_type_int16: v=nir_i2i32(&b,in->src[0].ssa);type=nir_type_int32;break;
            case nir_type_uint16: v=nir_u2u32(&b,in->src[0].ssa);type=nir_type_uint32;break;
            default: continue;
            }
            nir_src_rewrite(&in->src[0],v);
            nir_intrinsic_set_src_type(in,type);
            widened++;
         }
      }
      nir_metadata_preserve(impl,nir_metadata_control_flow);
   }
   static unsigned reports;
   if (widened && reports++<8)
      fprintf(stderr,"SweetNativeMetal: widened %u half vertex exports to full VPC registers\n",widened);
   return widened;
}
