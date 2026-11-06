// Guest virtual time from the logical work count (RUNTIME.md "Virtual guest time"), and cooperative interrupt
// delivery at guest function entries.
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <initializer_list>
#include "recomp.h"
#include "ultramodern/ultramodern.hpp"
#include "cpu_budget.h"
#include "cpu_profile.h"
#include "cpu_native.h"

void dequeue_external_messages(uint8_t *rdram);
extern "C" uint64_t n64host_golden_frame();
uint8_t *n64host_rdram;

namespace {
thread_local HostCpuBudget budget;

// The guest nanoseconds one charged entry advances, measured at the host's own start (RUNTIME.md "Virtual guest time"):
// over the first HOST_VI_HZ retraces (one second of VI time) entries are charged a provisional amount that only keeps
// the clock live; the measured charge is then the window's VI time over the entries it ran. Both inputs are logical
// (entries and retraces), so the result is the same on every host and load.
constexpr double kProvisionalEntriesPerVi = 100000;  // liveness bound for the window only, replaced once measured
constexpr double kViPeriodNs = 1e9 / HOST_VI_HZ;
std::atomic<uint64_t> window_entries{0};
std::atomic<uint64_t> window_start_frame{0};
std::atomic<bool> window_open{false};
std::atomic<bool> calibrated{false};
std::atomic<double> measured_ns{kViPeriodNs / kProvisionalEntriesPerVi};

double entry_charge_ns() {
    if (calibrated.load(std::memory_order_acquire)) return measured_ns.load(std::memory_order_relaxed);
    const uint64_t frame = n64host_golden_frame();
    if (!window_open.load()) {
        if (frame == 0) return measured_ns.load();
        window_start_frame.store(frame);
        window_open.store(true);
    }
    const uint64_t entries = ++window_entries;
    if (frame - window_start_frame.load() >= HOST_VI_HZ) {
        measured_ns.store(kViPeriodNs * HOST_VI_HZ / double(entries), std::memory_order_relaxed);
        calibrated.store(true, std::memory_order_release);
    }
    return measured_ns.load(std::memory_order_relaxed);
}

void service_interrupts() {
    if (!budget.interrupts_enabled) return;
    dequeue_external_messages(n64host_rdram);
    ultramodern::check_running_queue(n64host_rdram);
}
}

// Whether an entry of the recompiled function `name` is guest work: the original game code both sides compile, never
// the SDK range or a function the port binds to native code. N64Recomp suffixes a name that collides with the host's
// C library with _recomp (__udivdi3_recomp) and a kept libultra/libc original, whose plain name clashes with the
// runtime's headers and its _recomp with the runtime's own, with _kept (osContInit_kept); every other recompiled
// function is in symbols.toml, so an unknown name is a broken build.
extern "C" int n64host_charged(const char *name) {
    auto find = [](const char *key, size_t n) {
        size_t lo = 0, hi = sizeof host_functions / sizeof host_functions[0];
        while (lo < hi) {
            size_t mid = (lo + hi) / 2;
            int order = std::strncmp(host_functions[mid].name, key, n);
            if (order == 0 && host_functions[mid].name[n]) order = 1;
            if (order == 0) return int(host_functions[mid].charged);
            if (order < 0) lo = mid + 1; else hi = mid;
        }
        return -1;
    };
    size_t n = std::strlen(name);
    int charged = find(name, n);
    for (const char *suffix : {"_recomp", "_kept"}) {
        size_t s = std::strlen(suffix);
        if (charged < 0 && n > s && std::strcmp(name + n - s, suffix) == 0) charged = find(name, n - s);
    }
    if (charged >= 0) return charged;
    std::fprintf(stderr, "n64host: recompiled function %s is not in symbols.toml\n", name);
    std::_Exit(2);
}

// A guest service point: a function entry (charged when it is guest work), a spin loop's 1024th pass or an interrupt
// restore (never charged). Each delivers due interrupts.
extern "C" void n64host_cpu_tick(int charged) {
    if (!n64host_rdram || !ultramodern::this_thread()) return;
    if (charged) ultramodern::vclock_advance(budget.charge_entry(entry_charge_ns()));
    service_interrupts();
}

extern "C" void __wrap___osDisableInt_recomp(uint8_t *, recomp_context *ctx) {
    ctx->r2 = budget.disable_interrupts();
}

extern "C" void __wrap___osRestoreInt_recomp(uint8_t *, recomp_context *ctx) {
    budget.restore_interrupts(uint32_t(ctx->r4));
    if (budget.interrupts_enabled) n64host_cpu_tick(0);
}
