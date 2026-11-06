// SPDX-License-Identifier: MIT
// Host-only contract declarations, not an Apple SDK.
#pragma once
#include <stdint.h>
#include <stddef.h>
typedef uint32_t io_connect_t;typedef uint32_t io_service_t;typedef int kern_return_t;
typedef uintptr_t vm_address_t;typedef size_t vm_size_t;typedef uint32_t mach_port_t;typedef unsigned mach_msg_type_number_t;
typedef int *vm_region_info_t;
struct vm_region_basic_info_data_64_t {int protection;};
enum {KERN_SUCCESS=0,kIOReturnNoMemory=1,kIOReturnNoResources=2,kIOReturnBadArgument=3,kIOReturnTimeout=4,kIOReturnBusy=5,kIOReturnNotReady=6,kIOReturnNotFound=7,kIOReturnUnsupported=8,kIOReturnNotPermitted=9,VM_REGION_BASIC_INFO_COUNT_64=10,VM_REGION_BASIC_INFO_64=11,MACH_PORT_NULL=0,VM_PROT_READ=1,VM_PROT_WRITE=2,VM_FLAGS_ANYWHERE=1};
extern "C" {
kern_return_t IOConnectCallStructMethod(io_connect_t,unsigned,const void*,size_t,void*,size_t*);
unsigned IOServiceGetMatchingService(unsigned,void*);void *IOServiceMatching(const char*);
kern_return_t IOServiceOpen(unsigned,unsigned,unsigned,io_connect_t*);void IOObjectRelease(unsigned);void IOServiceClose(unsigned);
unsigned mach_task_self(void);void mach_port_deallocate(unsigned,unsigned);
kern_return_t vm_allocate(unsigned,uint64_t*,uint64_t,int);kern_return_t vm_deallocate(unsigned,uint64_t,uint64_t);
kern_return_t vm_region_64(unsigned,uint64_t*,uint64_t*,int,vm_region_info_t,unsigned*,unsigned*);
typedef struct MockSurface *IOSurfaceRef;
IOSurfaceRef IOSurfaceLookup(unsigned);void *IOSurfaceGetBaseAddress(IOSurfaceRef);
size_t IOSurfaceGetAllocSize(IOSurfaceRef);size_t IOSurfaceGetWidth(IOSurfaceRef);size_t IOSurfaceGetHeight(IOSurfaceRef);size_t IOSurfaceGetBytesPerRow(IOSurfaceRef);size_t IOSurfaceGetPlaneCount(IOSurfaceRef);
uint32_t IOSurfaceGetPixelFormat(IOSurfaceRef);uint32_t IOSurfaceGetID(IOSurfaceRef);
void CFRetain(const void*);void CFRelease(const void*);
}
