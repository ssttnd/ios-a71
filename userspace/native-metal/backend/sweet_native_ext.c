/* SPDX-License-Identifier: MIT
 * Included after air_metal.c; Gallium/NIR stay inside this library. */
#include <errno.h>
unsigned rwa_native_abi(void) { return SWEET_NATIVE_METAL_ABI; }
struct rwa_library {
   void *ctx;
   void *data;
   struct air_metallib lib;
};
struct rwa_library *rwa_library_open(const void *bytes, size_t size, char *why, size_t n)
{
   if (!bytes || size < 4 || memcmp(bytes,"MTLB",4)) {
      say(why,n,"reader: expected a Metal library container"); return NULL;
   }
   struct rwa_library *lib=calloc(1,sizeof(*lib));
   if (!lib) { say(why,n,"out of memory reading metallib");return NULL; }
   lib->ctx=ralloc_context(NULL);lib->data=malloc(size);
   if (!lib->ctx || !lib->data) { rwa_library_close(lib);return NULL; }
   memcpy(lib->data,bytes,size);
   const char *error=NULL;
   if (!air_metallib_parse(lib->ctx,lib->data,size,&lib->lib,&error)) {
      say(why,n,"reader: %s",error?error:"invalid metallib");rwa_library_close(lib);return NULL;
   }
   return lib;
}
void rwa_library_close(struct rwa_library *lib)
{
   if (lib) {ralloc_free(lib->ctx);free(lib->data);free(lib);}
}
unsigned rwa_library_count(const struct rwa_library *lib) {return lib?lib->lib.num_functions:0;}
const char *rwa_library_name(const struct rwa_library *lib,unsigned index)
{ return lib && index<lib->lib.num_functions?lib->lib.functions[index].name:NULL; }
int rwa_library_function(const struct rwa_library *lib,unsigned index,const void **bytes,size_t *size,unsigned *type)
{
   if(!lib || index>=lib->lib.num_functions || !bytes || !size || !type)return -1;
   const struct air_metallib_function *f=&lib->lib.functions[index];
   if(f->type<0 || f->type>2)return -1;
   *bytes=f->bitcode;*size=f->bitcode_size;*type=f->type+1;return 0;
}
int rwa_set_constants(struct rwa_device *d,unsigned stage,const struct rwa_constant *values,
                      unsigned count,char *why,size_t n)
{
   if(!d || stage>=3 || count>256 || (count&&!values)) {say(why,n,"invalid specialization table");return -1;}
   for(unsigned i=0;i<count;i++) if(!values[i].bytes || values[i].bytes>8) {
      say(why,n,"interface: function constant exceeds AIR's 64-bit value representation");return -1;
   }
   for(unsigned i=0;i<d->constant_count[stage];i++)free((void*)d->constants[stage][i].name);
   d->constant_count[stage]=0;
   for(unsigned i=0;i<count;i++) {
      d->constants[stage][i]=values[i];
      d->constants[stage][i].name=values[i].name?strdup(values[i].name):NULL;
      if(values[i].name&&!d->constants[stage][i].name){say(why,n,"out of memory copying constant name");return -1;}
      d->constant_count[stage]++;
   }
   return 0;
}
static unsigned native_constants(struct rwa_device *d,unsigned stage,void *ctx,const void *bytes,
                                  size_t size,struct air_constant_value **out)
{
   struct air_constant_value *declared=NULL;
   unsigned declarations=air_module_function_constants(ctx,bytes,size,&declared);
   *out=rzalloc_array(ctx,struct air_constant_value,declarations);
   unsigned count=0;
   for(unsigned i=0;i<declarations;i++) {
      for(unsigned j=0;j<d->constant_count[stage];j++) {
         const struct rwa_constant *v=&d->constants[stage][j];
         if(v->name ? declared[i].name && !strcmp(v->name,declared[i].name) : declared[i].index==v->index) {
            (*out)[count]=declared[i];(*out)[count++].bits=v->bits;break;
         }
      }
   }
   return count; // absent constants stay absent; never silently specialize to zero
}
int rwa_barrier(struct rwa_device *d,char *why,size_t n)
{
   if(!d || d->lost){say(why,n,"device lost while encoding memory barrier");return -1;}
   native_invalidate_constants(d);
   d->pipe->memory_barrier(d->pipe,PIPE_BARRIER_ALL);return 0;
}
static uint64_t native_now_ns(void)
{
   struct timespec now;clock_gettime(CLOCK_MONOTONIC_RAW,&now);
   return (uint64_t)now.tv_sec*1000000000ull+now.tv_nsec;
}
int rwa_render_draw(struct rwa_render *render,const struct rwa_draw *draw,char *why,size_t n)
{
   uint64_t began=native_now_ns();
   render->device->native_buffer_writes=false;
   int result=native_render_draw(render,draw,why,n);
   if(render->device->native_buffer_writes)
      native_invalidate_constants(render->device);
   render->device->native_draw_ns+=native_now_ns()-began;
   render->device->native_draw_count++;
   return result;
}
/* Which core runs the render thread. The ADT describes the six Cortex-A55
 * and two Cortex-A76 as one homogeneous cluster, so XNU places it anywhere.
 * TPIDR_EL0 bits 0-11 hold the current CPU number on this kernel. */
static unsigned native_cpu(void)
{
   uint64_t v;__asm__ volatile("mrs %0, TPIDR_EL0":"=r"(v));
   return (unsigned)(v&0xfff)&7;
}
static unsigned native_begin_cpu;
static uint64_t native_cpu_commands[8],native_cpu_encode_ns[8],native_cpu_draws[8],native_cpu_draw_count_at_begin;
int rwa_command_begin(struct rwa_device *d,char *why,size_t n)
{
   native_begin_cpu=native_cpu();native_cpu_draw_count_at_begin=d?d->native_draw_count:0;
   if(!d || d->lost || d->in_command){say(why,n,"device lost or command buffer already executing");return -1;}
   if(sweet_transport_command_begin()){say(why,n,"transport transaction already active");return -1;}
   native_invalidate_constants(d);
   memset(d->native_views_valid,0,sizeof(d->native_views_valid));
   memset(d->native_images_valid,0,sizeof(d->native_images_valid));
   memset(d->native_samplers_valid,0,sizeof(d->native_samplers_valid));
   d->native_begin_ns=native_now_ns();
   if(!d->native_window_ns)d->native_window_ns=d->native_begin_ns;
   d->in_command=true;return 0;
}
static int native_command_end(struct rwa_device *d,uint64_t *submitted,char *why,size_t n)
{
   if(!d || !d->in_command){say(why,n,"no command buffer is executing");return -1;}
   uint64_t encoded=native_now_ns();
   d->native_encode_ns+=encoded-d->native_begin_ns;
   native_cpu_commands[native_begin_cpu]++;
   native_cpu_encode_ns[native_begin_cpu]+=encoded-d->native_begin_ns;
   native_cpu_draws[native_begin_cpu]+=d->native_draw_count-native_cpu_draw_count_at_begin;
   // Changing/unbinding framebuffer closes its Gallium batch; no per-draw flush.
   d->pipe->set_framebuffer_state(d->pipe,&(struct pipe_framebuffer_state){0});
   util_unreference_framebuffer_state(&d->batch_fb);
   int result=0;
   if(submitted) {
      struct pipe_fence_handle *fence=NULL;
      d->pipe->flush(d->pipe,&fence,0);
      if(fence) {
         /* Timeout 0 still flushes freedreno's deferred submits into the
          * kernel (non-threaded msm fences are always "ready"); it only skips
          * the wait. The completion thread waits for the kernel fence. */
         d->screen->fence_finish(d->screen,NULL,fence,0);
         d->screen->fence_reference(d->screen,&fence,NULL);
      }
   } else result=finish_or_say_why(d,false,"command buffer",why,n);
   sweet_transport_command_end();
   if(submitted)*submitted=sweet_transport_submitted_fence();
   for(unsigned i=0;i<d->native_upload_count;i++)
      pipe_resource_reference(&d->native_uploads[i].cb.buffer,NULL);
   d->native_upload_count=0;
   // Work objects still retain every sampler/texture here. Clear raw sampler
   // state pointers before returning control to callers that can destroy them.
   d->in_command=false;
   for(unsigned stage=0;stage<PIPE_SHADER_TYPES;stage++) {
      struct rwa_bound pending={0};
      pending.num_views=d->native_pending_views[stage];
      pending.num_images=d->native_pending_images[stage];
      pending.num_samplers=d->native_pending_samplers[stage];
      unbind_stage(d,(enum pipe_shader_type)stage,&pending);
      d->native_pending_views[stage]=d->native_pending_images[stage]=d->native_pending_samplers[stage]=0;
   }
   uint64_t ended=native_now_ns();
   d->native_finish_ns+=ended-encoded;d->native_command_count++;
   if(ended-d->native_window_ns>=5000000000ull) {
      fprintf(stderr,"SweetNativeMetal: frontend window_ms=%llu commands=%llu draws=%llu encode_us=%llu draw_us=%llu finish_us=%llu\n",
         (unsigned long long)((ended-d->native_window_ns)/1000000),
         (unsigned long long)d->native_command_count,(unsigned long long)d->native_draw_count,
         (unsigned long long)(d->native_encode_ns/1000),(unsigned long long)(d->native_draw_ns/1000),
         (unsigned long long)(d->native_finish_ns/1000));
      fprintf(stderr,"SweetNativeMetal: render cpu commands=%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu encode_ms=%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu draws=%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu (cpu0-5 A55, cpu6-7 A76)\n",
         (unsigned long long)native_cpu_commands[0],(unsigned long long)native_cpu_commands[1],(unsigned long long)native_cpu_commands[2],(unsigned long long)native_cpu_commands[3],
         (unsigned long long)native_cpu_commands[4],(unsigned long long)native_cpu_commands[5],(unsigned long long)native_cpu_commands[6],(unsigned long long)native_cpu_commands[7],
         (unsigned long long)(native_cpu_encode_ns[0]/1000000),(unsigned long long)(native_cpu_encode_ns[1]/1000000),(unsigned long long)(native_cpu_encode_ns[2]/1000000),(unsigned long long)(native_cpu_encode_ns[3]/1000000),
         (unsigned long long)(native_cpu_encode_ns[4]/1000000),(unsigned long long)(native_cpu_encode_ns[5]/1000000),(unsigned long long)(native_cpu_encode_ns[6]/1000000),(unsigned long long)(native_cpu_encode_ns[7]/1000000),
         (unsigned long long)native_cpu_draws[0],(unsigned long long)native_cpu_draws[1],(unsigned long long)native_cpu_draws[2],(unsigned long long)native_cpu_draws[3],
         (unsigned long long)native_cpu_draws[4],(unsigned long long)native_cpu_draws[5],(unsigned long long)native_cpu_draws[6],(unsigned long long)native_cpu_draws[7]);
      memset(native_cpu_commands,0,sizeof native_cpu_commands);memset(native_cpu_encode_ns,0,sizeof native_cpu_encode_ns);memset(native_cpu_draws,0,sizeof native_cpu_draws);
      fprintf(stderr,"SweetNativeMetal: draw phases validation_surface_us=%llu fixed_state_us=%llu bindings_us=%llu draw_vbo_us=%llu\n",
         (unsigned long long)(d->native_phase_ns[0]/1000),(unsigned long long)(d->native_phase_ns[1]/1000),
         (unsigned long long)(d->native_phase_ns[2]/1000),(unsigned long long)(d->native_phase_ns[3]/1000));
      fprintf(stderr,"SweetNativeMetal: constant bindings calls=%llu reused=%llu\n",
         (unsigned long long)d->native_cb_calls,(unsigned long long)d->native_cb_reused);
      fprintf(stderr,"SweetNativeMetal: binding driver_us constants=%llu views=%llu images=%llu samplers=%llu reused_states=%llu\n",
         (unsigned long long)(d->native_cb_ns/1000),(unsigned long long)(d->native_views_ns/1000),
         (unsigned long long)(d->native_images_ns/1000),(unsigned long long)(d->native_samplers_ns/1000),
         (unsigned long long)d->native_binding_reused);
      fprintf(stderr,"SweetNativeMetal: constant uploads hits=%llu misses=%llu copy_us=%llu bytes=%llu\n",
         (unsigned long long)d->native_upload_hits,(unsigned long long)d->native_upload_misses,
         (unsigned long long)(d->native_upload_ns/1000),(unsigned long long)d->native_upload_bytes);
      fprintf(stderr,"SweetNativeMetal: bounded constant uploads count=%llu avoided_bytes=%llu\n",
         (unsigned long long)d->native_uniform_trimmed,(unsigned long long)d->native_uniform_trim_bytes);
      d->native_uniform_trimmed=d->native_uniform_trim_bytes=0;
      d->native_upload_hits=d->native_upload_misses=d->native_upload_ns=d->native_upload_bytes=0;
      d->native_cb_ns=d->native_views_ns=d->native_images_ns=d->native_samplers_ns=d->native_binding_reused=0;
      d->native_cb_calls=d->native_cb_reused=0;
      memset(d->native_phase_ns,0,sizeof(d->native_phase_ns));
      d->native_window_ns=ended;d->native_command_count=d->native_draw_count=0;
      d->native_encode_ns=d->native_draw_ns=d->native_finish_ns=0;
   }
   d->in_command=false;
   if(result)d->lost=true;
   return result;
}
int rwa_command_end(struct rwa_device *d,char *why,size_t n)
{
   return native_command_end(d,NULL,why,n);
}
int rwa_command_submit(struct rwa_device *d,uint64_t *fence,char *why,size_t n)
{
   if(!fence){say(why,n,"asynchronous command end needs a fence result");return -1;}
   *fence=0;
   return native_command_end(d,fence,why,n);
}
int rwa_command_poll(struct rwa_device *d,uint64_t fence,bool give_up,char *why,size_t n)
{
   if(!d){say(why,n,"no device for command completion");return -1;}
   int e=sweet_transport_poll_fence(fence);
   if(!e)return 0;
   if(e==ETIMEDOUT && !give_up)return 1;
   d->lost=true;
   if(e==ETIMEDOUT)
      say(why,n,"the GPU did not finish a command buffer within two seconds; its "
                "results are not what the shaders computed and the GPU is wedged");
   else
      say(why,n,"waiting for a command buffer's GPU fence failed (errno %d)",e);
   return -1;
}
int rwa_clear(struct rwa_device *d,struct rwa_texture *const *color,unsigned count,unsigned mask,
               const float values[8][4],struct rwa_texture *depth,const float *z,const unsigned *s,
               char *why,size_t n)
{
   if(!d || d->lost || count>8 || (mask>>count)){say(why,n,"invalid clear attachments");return -1;}
   for(unsigned i=0;i<count;i++)if(mask&(1u<<i)) {
      if(!color || !color[i]){say(why,n,"clear attachment %u is missing",i);return -1;}
      struct pipe_resource *res=color[i]->res;
      if(!(res->bind&PIPE_BIND_RENDER_TARGET) || util_format_is_pure_integer(res->format)) {
         say(why,n,"interface: clear requires a floating-point or normalized render target");return -1;
      }
      struct pipe_surface *surface=native_render_surface(color[i]);
      if(!surface){say(why,n,"failed to create clear surface");return -1;}
      union pipe_color_union value;
      memcpy(value.f,values[i],sizeof(value.f));
      d->pipe->clear_render_target(d->pipe,surface,&value,0,0,res->width0,res->height0,false);
      pipe_surface_reference(&surface,NULL);
   }
   if(z || s) {
      if(!depth){say(why,n,"depth/stencil clear has no attachment");return -1;}
      struct pipe_resource *res=depth->res;
      unsigned flags=(z?PIPE_CLEAR_DEPTH:0)|(s?PIPE_CLEAR_STENCIL:0);
      if((z&&!format_has_depth(res->format)) || (s&&!format_has_stencil(res->format))) {
         say(why,n,"depth/stencil clear aspect absent from format");return -1;
      }
      struct pipe_surface *surface=native_render_surface(depth);
      if(!surface){say(why,n,"failed to create depth clear surface");return -1;}
      d->pipe->clear_depth_stencil(d->pipe,surface,flags,z?*z:0,s?*s:0,0,0,res->width0,res->height0,false);
      pipe_surface_reference(&surface,NULL);
   }
   return d->in_command?0:finish_or_say_why(d,true,"clear",why,n);
}
