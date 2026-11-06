/* SPDX-License-Identifier: MIT */
#import <Foundation/Foundation.h>
#include <IOSurface/IOSurfaceRef.h>
#include <stdio.h>
#include <string.h>
#include "redwood_drawable.h"
#include "sweet_native_ext.h"

uint32_t rwd_surface_id(void *surface) {return surface?IOSurfaceGetID((IOSurfaceRef)surface):0;}
int rwd_import_available(void) {return rwa_native_abi()==SWEET_NATIVE_METAL_ABI;}
struct rwa_texture *rwd_texture_for_surface(struct rwa_device *device,void *surface,unsigned long plane,
    const struct rwd_texture_shape *want,struct rwd_surface *took,char *why,size_t n)
{
    IOSurfaceRef s=(IOSurfaceRef)surface;
    if(took)memset(took,0,sizeof(*took));
#define REFUSE(...) do {snprintf(why,n,__VA_ARGS__);return NULL;} while(0)
    if(!device || !s || !want)REFUSE("IOSurface import requires a device, surface and descriptor");
    if(plane || IOSurfaceGetPlaneCount(s))REFUSE("interface: planar IOSurface textures are not implemented");
    size_t width=IOSurfaceGetWidth(s),height=IOSurfaceGetHeight(s),pitch=IOSurfaceGetBytesPerRow(s);
    unsigned bpp=0;uint32_t fourcc=0;
    switch(want->pixel_format) {
    case 1:case 10:fourcc='A008';bpp=1;break;
    case 30:fourcc='LA08';bpp=2;break;
    case 70:case 71:fourcc='RGBA';bpp=4;break;
    case 80:case 81:fourcc='BGRA';bpp=4;break;
    case 115:fourcc='RGhA';bpp=8;break;
    default:REFUSE("interface: Metal format %u / IOSurface %#x (%zux%zu pitch %zu) is not a supported linear import",want->pixel_format,IOSurfaceGetPixelFormat(s),width,height,pitch);
    }
    if(want->width!=width || want->height!=height || want->type!=2 || want->depth>1 ||
       want->array_length>1 || want->mip_levels>1 || want->sample_count>1)
        REFUSE("interface: IOSurface texture must match a single 2D plane, mip and sample");
    if(IOSurfaceGetPixelFormat(s)!=fourcc || width>UINT32_MAX || height>UINT32_MAX ||
       pitch>UINT32_MAX-63 || width>SIZE_MAX/bpp || pitch<width*bpp)
        REFUSE("interface: IOSurface %#x for Metal %u expected %#x, %zux%zu pitch %zu; incompatible A618 linear layout",IOSurfaceGetPixelFormat(s),want->pixel_format,fourcc,width,height,pitch);
    // A618 samples linear rows only at 64-byte pitches. A surface another
    // process laid out with a shorter row (SpringBoard's LA08 glyphs ask for
    // 210/224 bytes) is read through a shadow at the rounded pitch, which the
    // transport refills from the surface whenever its seed changes (the same
    // rounding rule is in sweet_drm.cpp surface_description). Only for
    // textures the GPU reads: a GPU write would not reach the surface.
    size_t gpu_pitch=pitch;
    if(pitch&63) {
        if(want->usage&(2|4))
            REFUSE("interface: IOSurface %u %zux%zu pitch %zu is not 64-byte aligned and the texture is written (usage %#x); a read-only shadow cannot carry GPU writes back",
                   IOSurfaceGetID(s),width,height,pitch,want->usage);
        gpu_pitch=(pitch+63)&~(size_t)63;
    }
    uintptr_t pixels=(uintptr_t)IOSurfaceGetBaseAddress(s),base=pixels&~(uintptr_t)16383;
    size_t bytes=IOSurfaceGetAllocSize(s),offset=pixels-base;
    if(!base || offset>=bytes || (height && pitch>(bytes-offset)/height))
        REFUSE("interface: IOSurface rows extend beyond its mapped allocation");
    unsigned id=IOSurfaceGetID(s);
    if(!id)REFUSE("IOSurface has no shareable ID");
    // The winsys looks up this ID only inside this process, pins its actual CPU
    // mapping through ABI selector16 and retains the surface through GEM_CLOSE.
    struct rwa_texture *texture=rwa_texture_from_surface(device,(const struct rwa_texture_shape*)want,
                                                         id,(unsigned)offset,(unsigned)gpu_pitch,why,n);
    static unsigned shadow_imports;
    if(texture && gpu_pitch!=pitch && shadow_imports++<16)
        fprintf(stderr,"SweetNativeMetal: IOSurface %u %#x %zux%zu pitch %zu read through a %zu-byte-row shadow\n",
                id,fourcc,width,height,pitch,gpu_pitch);
    static unsigned rgha_imports;
    if(texture && want->pixel_format==115 && rgha_imports++<8)
        fprintf(stderr,"SweetNativeMetal: RGBA16F IOSurface %u imported, %zux%zu pitch %zu usage %#x\n",id,width,height,pitch,want->usage);
    static unsigned rg8_imports;
    if(texture && want->pixel_format==30 && rg8_imports++<4)
        fprintf(stderr,"SweetNativeMetal: RG8 IOSurface %u imported, %zux%zu pitch %zu\n",id,width,height,pitch);
    if(texture && took)*took=(struct rwd_surface){id,0,bytes,(unsigned)offset,(unsigned)gpu_pitch,
                                                (unsigned)width,(unsigned)height,fourcc,0};
    return texture;
#undef REFUSE
}
