// SPDX-License-Identifier: MIT
// Wire/lifetime contract only. This model executes no Adreno instructions.
#include "../transport/sweet_drm.cpp"
#include <cassert>
#include <map>
#include <sys/wait.h>
struct MockSurface {uint64_t base;size_t size;unsigned references,id;};
namespace model {
struct Pin {uint64_t address,bytes,va,last;};
std::map<uint64_t,uint64_t> regions;
std::map<uint64_t,Pin> pins;
std::map<uint32_t,MockSurface> surfaces;
uint64_t nextHandle=1,nextFence=UINT64_C(0x100000000),completed=0;
unsigned submits=0,waits=0,closes=0,failSubmit=0;
bool forceTimeout=false;
std::vector<NativeAdrenoAbiSubmitResources> captured;
}
extern "C" {
unsigned mach_task_self(void){return 42;}
void mach_port_deallocate(unsigned,unsigned){}
void *IOServiceMatching(const char *name){assert(!strcmp(name,"AGXAccelerator"));return (void*)1;}
unsigned IOServiceGetMatchingService(unsigned,void *match){assert(match);return 7;}
kern_return_t IOServiceOpen(unsigned service,unsigned task,unsigned type,io_connect_t *out){assert(service==7&&task==42&&type==0x4147);*out=9;return 0;}
void IOObjectRelease(unsigned){}
void IOServiceClose(unsigned){model::closes++;}
kern_return_t vm_allocate(unsigned task,uint64_t *out,uint64_t size,int flags){
    assert(task==42&&flags==VM_FLAGS_ANYWHERE&&(size&16383)==0);
    void *p=nullptr;if(posix_memalign(&p,16384,size))return kIOReturnNoMemory;
    memset(p,0,size);*out=(uintptr_t)p;model::regions[*out]=size;return 0;
}
kern_return_t vm_deallocate(unsigned,uint64_t address,uint64_t size){
    assert(model::regions.at(address)==size);
    for(auto &p:model::pins)assert(p.second.address!=address);
    model::regions.erase(address);free((void*)(uintptr_t)address);return 0;
}
kern_return_t vm_region_64(unsigned,uint64_t *address,uint64_t *length,int,vm_region_info_t data,unsigned*,unsigned *object){
    for(auto &r:model::regions)if(*address>=r.first && *address<r.first+r.second){
        *address=r.first;*length=r.second;((vm_region_basic_info_data_64_t*)data)->protection=VM_PROT_READ|VM_PROT_WRITE;*object=0;return 0;
    }return kIOReturnBadArgument;
}
IOSurfaceRef IOSurfaceLookup(unsigned id){auto found=model::surfaces.find(id);if(found==model::surfaces.end())return nullptr;found->second.references++;return &found->second;}
void *IOSurfaceGetBaseAddress(IOSurfaceRef s){return (void*)(uintptr_t)(s->base+4096);}
size_t IOSurfaceGetAllocSize(IOSurfaceRef s){return s->size;}
size_t IOSurfaceGetWidth(IOSurfaceRef){return 16;}
size_t IOSurfaceGetHeight(IOSurfaceRef){return 16;}
size_t IOSurfaceGetBytesPerRow(IOSurfaceRef){return 64;}
size_t IOSurfaceGetPlaneCount(IOSurfaceRef){return 0;}
uint32_t IOSurfaceGetPixelFormat(IOSurfaceRef){return 0x42475241;}
uint32_t IOSurfaceGetID(IOSurfaceRef s){return s->id;}
void CFRetain(const void *s){((MockSurface*)s)->references++;}
void CFRelease(const void *s){assert(((MockSurface*)s)->references);((MockSurface*)s)->references--;}
kern_return_t IOConnectCallStructMethod(io_connect_t connection,unsigned method,const void *input,size_t size,void *output,size_t *outSize){
    assert(connection==9);
#define INPUT(type) assert(size==sizeof(type));auto p=(const type*)input
#define OUTPUT(type,expression) assert(output&&outSize&&*outSize==sizeof(type));*(type*)output=expression
    switch(method){
    case NativeAdrenoMethodQuery:{assert(!input&&!size);NativeAdrenoAbiParams p{1,32,36,7,UINT64_C(1)<<28};OUTPUT(NativeAdrenoAbiParams,p);return 0;}
    case NativeAdrenoMethodGetDeviceInfo:{NativeAdrenoAbiDeviceInfo info{};info.chip_id=0x06010800;info.gmem_bytes=512*1024;info.va_start=0x4000000;info.va_size=64*1024*1024;OUTPUT(NativeAdrenoAbiDeviceInfo,info);return 0;}
    case NativeAdrenoMethodBoImport:{INPUT(NativeAdrenoAbiBoImport);assert(model::regions.at(p->address)>=p->bytes);uint64_t handle=model::nextHandle++;model::pins[handle]={p->address,p->bytes,0,0};NativeAdrenoAbiBoAllocReply reply{handle};OUTPUT(NativeAdrenoAbiBoAllocReply,reply);return 0;}
    case NativeAdrenoMethodBoMap:{INPUT(NativeAdrenoAbiBoMap);auto &b=model::pins.at(p->handle);assert(p->permissions==3&&!p->writeback&&p->shareability==2&&!p->reserved&&!(p->iova&4095));
        for(auto &other:model::pins)if(other.second.va)assert(p->iova+b.bytes<=other.second.va||p->iova>=other.second.va+other.second.bytes);
        b.va=p->iova;NativeAdrenoAbiBoMapReply reply{p->iova};OUTPUT(NativeAdrenoAbiBoMapReply,reply);return 0;}
    case NativeAdrenoMethodBoFree:{INPUT(NativeAdrenoAbiBoFree);assert(model::pins.at(p->handle).last<=model::completed);model::pins.erase(p->handle);return 0;}
    case NativeAdrenoMethodWaitFence:{INPUT(NativeAdrenoAbiWaitFence);model::waits++;if(p->fence<=model::completed)return 0;if(!p->timeout_ns||model::forceTimeout)return kIOReturnTimeout;model::completed=p->fence;return 0;}
    case NativeAdrenoMethodSubmitResources:{INPUT(NativeAdrenoAbiSubmitResources);model::submits++;if(model::failSubmit==model::submits)return kIOReturnBadArgument;
        assert(!p->reserved&&!p->commands.reserved&&!p->commands.wait_fence&&!p->commands.wait_timeout_ns);
        assert(p->commands.buffer_count&&p->commands.buffer_count<=16&&p->resource_count&&p->resource_count<=1024);
        uint64_t fence=++model::nextFence;
        for(unsigned i=0;i<p->resource_count;i++){assert(p->resources[i].access&&!(p->resources[i].access&~3u));assert(!p->resources[i].reserved);model::pins.at(p->resources[i].handle).last=fence;}
        for(unsigned i=0;i<p->commands.buffer_count;i++){auto &ib=p->commands.buffers[i];auto &b=model::pins.at(ib.handle);assert(ib.iova>=b.va&&ib.bytes<=b.bytes-(ib.iova-b.va));bool listed=false;for(unsigned j=0;j<p->resource_count;j++)listed|=p->resources[j].handle==ib.handle&&(p->resources[j].access&1);assert(listed);}
        model::captured.push_back(*p);NativeAdrenoAbiSubmitReply reply{fence};OUTPUT(NativeAdrenoAbiSubmitReply,reply);return 0;}
    case NativeAdrenoMethodCreateQueue:{INPUT(NativeAdrenoAbiQueue);assert(!p->queue);NativeAdrenoAbiQueueReply reply{1};OUTPUT(NativeAdrenoAbiQueueReply,reply);return 0;}
    case NativeAdrenoMethodCloseQueue:{INPUT(NativeAdrenoAbiQueue);assert(p->queue==1);return 0;}
    default:assert(!"unexpected user-client selector");return kIOReturnUnsupported;
    }
#undef INPUT
#undef OUTPUT
}
}
static int gpu(){int fd=drmOpenWithType("msm",nullptr,DRM_NODE_RENDER);assert(fd>=0);return fd;}
static uint32_t alloc(int fd){drm_msm_gem_new n{};n.size=16384;n.flags=MSM_BO_WC;assert(!drmCommandWriteRead(fd,DRM_MSM_GEM_NEW,&n,sizeof n));assert(n.handle);return n.handle;}
static int close_bo(int fd,uint32_t handle){drm_gem_close p{handle,0};return drmIoctl(fd,DRM_IOCTL_GEM_CLOSE,&p);}
struct Submit {
    drm_msm_gem_submit s{};
    drm_msm_gem_submit_bo bos[2]{};
    drm_msm_gem_submit_cmd cmds[17]{};
    Submit(uint32_t a,uint32_t b,unsigned count=1){
        bos[0]={MSM_SUBMIT_BO_READ,a,0};bos[1]={MSM_SUBMIT_BO_READ|MSM_SUBMIT_BO_WRITE,b,0};
        for(auto &c:cmds){c.type=MSM_SUBMIT_CMD_BUF;c.submit_idx=0;c.size=4;}
        s.flags=MSM_PIPE_3D0;s.nr_bos=2;s.nr_cmds=count;s.bos=(uintptr_t)bos;s.cmds=(uintptr_t)cmds;
    }
    int run(int fd){return drmCommandWriteRead(fd,DRM_MSM_GEM_SUBMIT,&s,sizeof s);}
};
static void split_and_fences(){
    int fd=gpu();auto a=alloc(fd),b=alloc(fd);Submit s(a,b,17);assert(!s.run(fd));
    assert(model::submits==2&&model::waits==0&&s.s.fence==1);
    assert(model::captured[0].commands.buffer_count==16&&model::captured[1].commands.buffer_count==1);
    assert(model::captured[0].resource_count==2&&model::captured[1].resource_count==2);
    drm_msm_gem_cpu_prep prep{};prep.handle=b;prep.op=MSM_PREP_READ|MSM_PREP_NOSYNC;
    assert(drmCommandWrite(fd,DRM_MSM_GEM_CPU_PREP,&prep,sizeof prep)==-EBUSY);
    drm_msm_wait_fence f{};f.fence=1;f.queueid=1;
    assert(drmCommandWrite(fd,DRM_MSM_WAIT_FENCE,&f,sizeof f)==-EINVAL); // pending fence belongs to queue0
    f.queueid=0;timespec now{};clock_gettime(CLOCK_MONOTONIC_RAW,&now);f.timeout={now.tv_sec+1,now.tv_nsec};
    assert(!drmCommandWrite(fd,DRM_MSM_WAIT_FENCE,&f,sizeof f));
    assert(model::completed>UINT32_MAX); // public MSM fence was a mapped ID, not truncation
    assert(!close_bo(fd,a)&&!close_bo(fd,b)&&model::regions.empty());close(fd);
}
static void prevalidate_all(){
    int fd=gpu();auto a=alloc(fd),b=alloc(fd);Submit s(a,b,17);s.cmds[16].size=16388;
    assert(s.run(fd)==-EINVAL&&model::submits==0);
    s.cmds[16].size=4;s.bos[1].handle=a;assert(s.run(fd)==-EINVAL&&model::submits==0);
    assert(!close_bo(fd,a)&&!close_bo(fd,b));close(fd);
}
static void surface_lifetime(){
    int fd=gpu();uint64_t address=0;assert(!vm_allocate(42,&address,16384,VM_FLAGS_ANYWHERE));
    model::surfaces[51]={address,16384,1,51};struct redwood_drm_surface desc{};desc.surface=51;
    assert(!redwood_drm_surface(fd,&desc)&&desc.offset==4096&&desc.size==16384&&desc.row_bytes==64);
    uint32_t handle=0,again=0;assert(!drmPrimeFDToHandle(fd,51,&handle));assert(model::surfaces[51].references==2);
    assert(!drmPrimeFDToHandle(fd,51,&again)&&again==handle);
    assert(!close_bo(fd,handle)&&model::surfaces[51].references==1);
    assert(model::regions.size()==1); // imported storage belongs to IOSurface
    assert(!vm_deallocate(42,address,16384));close(fd);
}
static void timeout_retains(){
    int fd=gpu();auto a=alloc(fd),b=alloc(fd);Submit s(a,b);assert(!s.run(fd));model::forceTimeout=true;
    size_t pages=model::regions.size();assert(close_bo(fd,b)==-1&&errno==ETIMEDOUT);
    assert(model::regions.size()==pages&&model::pins.size()==2);
    auto c=alloc(fd);assert(c!=b); // timed-out identity/pages cannot be recycled
    close(fd);
}
static void partial_submit_lost(){
    int fd=gpu();auto a=alloc(fd),b=alloc(fd);model::failSubmit=2;Submit s(a,b,17);
    assert(s.run(fd)==-EINVAL&&model::submits==2&&model::pins.size()==2);
    assert(close_bo(fd,a)==-1);assert(model::pins.size()==2&&model::regions.size()==2);close(fd);
}
int main(){
    const struct {const char *name;void (*run)();} cases[]={
        {"split residency, mapped fences, no eager wait",split_and_fences},
        {"all IBs and duplicates validated before submit",prevalidate_all},
        {"IOSurface prefix and retained backing",surface_lifetime},
        {"timeout quarantines live storage",timeout_retains},
        {"partial submission preserves live storage",partial_submit_lost}};
    for(auto &c:cases){pid_t child=fork();assert(child>=0);if(!child){c.run();exit(0);}int status=0;assert(waitpid(child,&status,0)==child);if(!WIFEXITED(status)||WEXITSTATUS(status)){fprintf(stderr,"FAIL: %s\n",c.name);return 1;}printf("PASS: %s\n",c.name);fflush(stdout);}
    puts("Transport model passed; no GPU commands executed.");
}
