// Logging stand-ins for libultra internals absent from the pinned N64ModernRuntime.
// Each one is a recorded gap, not an implementation.
#include "recomp.h"
#include <stdio.h>
#include <stdlib.h>
// A guest call into a function nothing implements stops the host with its name: returning would leave the guest running on
// a result nobody computed, and a boot-idle run that idles after that proves nothing.
void n64host_unimplemented(const char *name) {
    fprintf(stderr, "n64host: unimplemented %s\n", name);
    fflush(stderr);
    _Exit(5);
}
#define GAP(f) void f(uint8_t* rdram, recomp_context* ctx) { n64host_unimplemented(#f); }
GAP(__osAiDeviceBusy_recomp) GAP(__osDpDeviceBusy_recomp)
GAP(__osSpDeviceBusy_recomp) GAP(__osInsertTimer_recomp)

// Runtime exit() invokes destructors of still-running workers and obscures this diagnostic with SIGABRT.
void __wrap_switch_error(const char *function, uint32_t pc, uint32_t table) {
    fprintf(stderr, "n64host: incomplete generated switch function=%s pc=0x%08X table=0x%08X\n",
            function, pc, table);
    _Exit(4);
}
