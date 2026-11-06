/* SPDX-License-Identifier: MIT
 * AIR's generated interstage semantic is the link key, not declaration order.
 * CoreAnimation can pair independently specialized vertex/fragment modules.
 */
static bool
native_link_stages(nir_shader *vs, const struct air_shader_info *vinfo,
                   nir_shader *fs, const struct air_shader_info *finfo,
                   char *why, size_t why_size)
{
   NIR_PASS(_, fs, nir_remove_dead_variables, nir_var_shader_in, NULL);
   unsigned remapped = 0;
   nir_foreach_shader_in_variable(input, fs) {
      unsigned old = input->data.location;
      if (old < VARYING_SLOT_VAR0)
         continue; /* built-in interfaces have fixed Gallium locations */
      const struct air_varying *consumer = NULL, *producer = NULL;
      for (unsigned i=0;i<finfo->num_varyings;i++)
         if (finfo->varyings[i].slot == old) {consumer=&finfo->varyings[i];break;}
      if (consumer && consumer->name)
         for (unsigned i=0;i<vinfo->num_varyings;i++)
            if (vinfo->varyings[i].name && !strcmp(consumer->name,vinfo->varyings[i].name)) {
               producer=&vinfo->varyings[i];break;
            }
      if (!producer) {
         snprintf(why,why_size,"interstage link: %s reads %s with no output from %s",
                  finfo->entry_point,consumer && consumer->name?consumer->name:"an unnamed varying",
                  vinfo->entry_point);
         return false;
      }
      nir_variable *output = NULL;
      nir_foreach_shader_out_variable(candidate, vs)
         if (candidate->data.location == producer->slot) {output=candidate;break;}
      if (!output || glsl_get_bit_size(output->type)!=glsl_get_bit_size(input->type) ||
          glsl_get_vector_elements(output->type)<glsl_get_vector_elements(input->type)) {
         snprintf(why,why_size,"interstage link: incompatible type for %s",consumer->name);
         return false;
      }
      remapped += old != producer->slot;
      input->data.location=producer->slot;
   }
   // Keep a bounded startup record of actual pairings, including identity links.
   // No Apple shader bytes or application data are emitted.
   static unsigned traces;
   if (traces++ < 24 || remapped)
      fprintf(stderr,"SweetNativeMetal: stage link %s -> %s; remapped %u live inputs\n",
              vinfo->entry_point,finfo->entry_point,remapped);
   return true;
}
