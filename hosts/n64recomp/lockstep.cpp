// Lockstep recorder for the oracle host: a test instrument only, inert unless N64R_LOCKSTEP_DIR is set.
// It writes the oracle half of a lockstep comparison against a native port (the port's own tool compares):
//   lockstep.jsonl  one JSON object per line:
//     meta  side, ram, page, bytes ("canonical-be"), hash, calls (the calls kept per caller and function), frame
//           ("poll": a call's frame is the poll index, the unit the port's calls.jsonl stamps); replaced = the vrams of the runtime
//           replacements (main.cpp's table, generated as funcs/replaced_funcs.inl): no body of theirs runs, so no call
//           of theirs is ever recorded here
//     hit   the first entry of a function: n (point number), fn, thr, vi, sp, pages = [[page, hash], ...] for
//           every 4 KiB page whose hash changed since the previous point (all pages at the first point)
//     poll  every controller poll of a TAS movie: poll (the index, from 1), vi, ns (the virtual guest clock at the poll,
//           the port's n64c_clock_peek_ns), pad = the word each controller
//           answers (buttons << 16 | x << 8 | y), pages = the pages changed since the previous poll (all at the first)
//     pair  a (thread, caller, function) pair at its first call: id, thr, caller, fn
//     tot   at each poll, before its `poll` line: d = [[pair id, calls so far], ...] for every pair whose count moved
//     call  one of the first N64R_LOCKSTEP_CALLS (8) calls that each caller makes of a function on one thread, then every call at a power-of-two
//           ordinal (a ring the port keeps at the same ordinals): i (the function's
//           running call number), fn, caller (the nearest hooked function on this guest thread's stack, "?" for
//           none), o (the ordinal among that caller's calls of fn on this thread, every call counted), frame (the poll index this
//           call falls in: the polls read so far, the port's reach_poll; never the retrace count), thr,
//           a = a0..a3, f12, f14 (raw 64-bit), sp. The port writes the same keys (shim/boot/trace.c), so the two
//           sides align call by call whatever phase their polls are in.
//     ret   that call's return: fn, i, r = v0, v1, f0 (raw 64-bit), m = the 16-word windows the argument registers
//           a0..a3 point at in RDRAM, read at the return (an RDRAM address inside a window reads pppppppp)
//     watch a word of N64R_LOCKSTEP_WATCH = ADDR[@FROM-TO],... (up to 128 words) changed between two hooks: w (the
//           word), poll, at (enter|ret), fn, old, new; a word with @FROM-TO only reports its changes in polls FROM..TO
// N64R_LOCKSTEP_CALLS_POLLS=FROM-TO also records every call that falls in those polls (all ordinals, beside the ring),
// so a short stretch is recorded call by call (the port's N64C_CALLS_POLLS keeps the same).
// The record is hashes only: no page bytes. Bytes come from a dump: N64R_LOCKSTEP_DUMP=POLL:PAGE,PAGE,... replays the
// same movie, records nothing (lockstep.jsonl is left as it is), and at poll POLL writes
//   dump/POLL/dump.bin  each named page: u32 page, u32 0, u64 hash (little-endian), then its 4096 bytes
// (written beside it, then renamed) and ends the process (exit 0), so the dump is complete when the host has exited.
// Pages are hashed and dumped in canonical byte order (big-endian, as the cartridge-era CPU saw them): RDRAM keeps
// each 32-bit word in host order (byte accesses use address ^ 3), so every word is byte-swapped back first.
// The hash is FNV-1a over the little-endian 64-bit words of those bytes.
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <mutex>
#include <string>
#include <unordered_map>
#include <vector>

extern "C" uint32_t n64host_replaced_vram(size_t i);  // main.cpp: the runtime replacement table
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>

extern "C" uint64_t n64host_golden_frame();
extern "C" volatile unsigned long long n64host_tas_poll;  // golden.cpp: the polls the guest has read

namespace {
constexpr uint32_t PAGE = 4096;
struct Fn { unsigned calls = 0; bool hit = false; };
struct Frame { const char *name; uint32_t sp; int64_t call; uint32_t a[4]; };
// A pair: one guest thread's calls from one caller to one function. Ordinals are per thread, as the port's
// shim/boot/trace.c keys them, so a function two threads call keeps one aligned sequence per thread whatever the
// threads' interleaving on either side.
struct CallerFn {
    const char *caller, *fn;
    long thr;
    bool operator==(const CallerFn &o) const { return caller == o.caller && fn == o.fn && thr == o.thr; }
};
struct CallerFnHash {
    size_t operator()(const CallerFn &k) const {
        return (std::hash<const void *>()(k.caller) * 0x9E3779B97F4A7C15ull ^ std::hash<const void *>()(k.fn)) * 31u +
               std::hash<long>()(k.thr);
    }
};
// Every call counted, recorded or not: n is the pair's next ordinal, told its count at the last `tot` line, id the
// number its `pair` line gave it.
struct PairCount { unsigned n = 0, told = 0, id = 0; };
std::mutex lock;
int log_fd = -1;
int64_t dump_poll = -1;  // N64R_LOCKSTEP_DUMP: the poll whose named pages are dumped; -1 records
uint32_t ram_bytes = 0x800000, max_calls = 8, points = 0;
// The watched words: physical address, value last seen, lines written, the polls whose changes are reported.
struct Watch { uint32_t phys = 0, value = 0; unsigned lines = 0; unsigned long long from = 0, to = ~0ull; };
std::vector<Watch> &watches = *new std::vector<Watch>;
bool watching = false;
unsigned long long window_from = 1, window_to = 0;  // N64R_LOCKSTEP_CALLS_POLLS
// Leaked on purpose: guest threads still call in while main returns and static destructors run.
std::unordered_map<const char *, Fn> &fns = *new std::unordered_map<const char *, Fn>;
// Calls each caller has made of each function on each thread, recorded or not: the call's ordinal, and the per-poll
// totals. `pairs` lists them in id order, so the totals walk only the pairs seen.
std::unordered_map<CallerFn, PairCount, CallerFnHash> &ordinals = *new std::unordered_map<CallerFn, PairCount, CallerFnHash>;
std::vector<PairCount *> &pairs = *new std::vector<PairCount *>;
std::vector<uint32_t> &dump_pages = *new std::vector<uint32_t>;
std::string &dump_dir = *new std::string;
thread_local std::vector<Frame> frames;
// The pages one stream of points saw last: each stream (function hits, controller polls) lists at a point the 4 KiB
// pages whose hash changed since its own previous point (every page at its first), so the poll stream reads the same
// whichever functions the build hooks.
struct Pages { std::vector<uint64_t> last; std::vector<uint8_t> shadow; bool started = false; };
Pages &hit_pages = *new Pages;
Pages &poll_pages = *new Pages;

long tid() {  // once per thread: every call keys its pair by it
    thread_local const long id = (long)syscall(SYS_gettid);
    return id;
}
void out(int fd, const void *data, size_t size) {
    const char *p = static_cast<const char *>(data);
    while (size) {
        ssize_t n = write(fd, p, size);
        if (n <= 0) return;
        p += n;
        size -= (size_t)n;
    }
}
void out_line(const std::string &line) { out(log_fd, line.data(), line.size()); }
std::string hex32(uint32_t v) { char b[16]; std::snprintf(b, sizeof b, "\"%08x\"", v); return b; }
std::string hex64(uint64_t v) { char b[24]; std::snprintf(b, sizeof b, "\"%016llx\"", (unsigned long long)v); return b; }
uint32_t word(const uint8_t *rdram, uint32_t phys) { uint32_t w; std::memcpy(&w, rdram + phys, 4); return w; }

// The calls of one (caller, function) pair a run keeps: the first max_calls, then every power of two (a ring both sides
// keep at the same ordinals, so the later calls of a long movie align too; the port's shim/boot/trace.c does the same).
bool keeps(unsigned ordinal) {
    return ordinal < max_calls || (ordinal & (ordinal - 1)) == 0 ||
           (n64host_tas_poll >= window_from && n64host_tas_poll <= window_to);
}

// Memory a call leaves its results in: at the return of a recorded call, every argument register holding an aligned
// address inside RDRAM names a WINDOW-word window, read as the 32-bit values the original's code sees. A word of the
// window that is itself an RDRAM address reads `pppppppp` (the port's native addresses are not the original's; that it
// is a pointer is what both sides agree on). Keys are the argument registers a0..a3.
constexpr uint32_t WINDOW = 16;
bool in_ram(uint32_t guest, uint32_t bytes) {
    const uint32_t seg = guest >> 29, phys = guest & 0x1FFFFFFFu;
    return (seg == 4 || seg == 5) && phys + bytes <= ram_bytes;
}
std::string windows(const uint8_t *rdram, const uint32_t *a) {
    std::string out = "{";
    const char *sep = "";
    for (int k = 0; k < 4; k++) {
        if ((a[k] & 3) || !in_ram(a[k], WINDOW * 4)) continue;
        out += std::string(sep) + "\"" + std::to_string(k) + "\":\"";
        sep = ",";
        for (uint32_t j = 0; j < WINDOW; j++) {
            const uint32_t w = word(rdram, (a[k] & 0x1FFFFFFFu) + j * 4);
            char b[12];
            if (in_ram(w, 1)) std::snprintf(b, sizeof b, "pppppppp");
            else std::snprintf(b, sizeof b, "%08x", w);
            out += b;
        }
        out += "\"";
    }
    return out + "}";
}

// The canonical big-endian bytes of RDRAM page pg (each host-order word byte-swapped back) into page, and their hash.
uint64_t canonical(const uint8_t *rdram, uint32_t pg, uint8_t *page) {
    uint64_t h = 0xcbf29ce484222325ull;
    for (uint32_t i = 0; i < PAGE; i += 4) {
        uint32_t w = __builtin_bswap32(word(rdram, pg * PAGE + i));
        std::memcpy(page + i, &w, 4);
    }
    for (uint32_t i = 0; i < PAGE; i += 8) {
        uint64_t w;
        std::memcpy(&w, page + i, 8);
        h = (h ^ w) * 0x100000001b3ull;
    }
    return h;
}

// N64R_LOCKSTEP_DUMP=POLL:PAGE,PAGE,...: the dump replay. Nothing is recorded; the poll point at POLL writes the named
// pages and ends the process.
int open_dump(const std::string &dir, const char *spec) {
    char *end;
    const unsigned long long poll = std::strtoull(spec, &end, 10);
    bool ok = *end == ':' && poll > 0 && ram_bytes;
    while (ok && *end && *++end) {
        const char *at = end;
        const unsigned long pg = std::strtoul(at, &end, 10);
        ok = end != at && (*end == ',' || !*end) && pg < ram_bytes / PAGE;
        if (ok) dump_pages.push_back((uint32_t)pg);
    }
    if (!ok || dump_pages.empty()) {
        std::fprintf(stderr, "n64host: lockstep dump off (N64R_LOCKSTEP_DUMP is POLL:PAGE,..., got %s)\n", spec);
        return 0;
    }
    dump_poll = (int64_t)poll;
    dump_dir = dir + "/dump";
    return 1;
}

// The dump at poll dump_poll (lock held): dump/POLL/dump.bin, then the process ends; its exit is the dump's completion.
void dump(const uint8_t *rdram) {
    static uint8_t page[PAGE];
    mkdir(dump_dir.c_str(), 0777);
    const std::string folder = dump_dir + "/" + std::to_string(dump_poll), part = folder + "/dump.bin.part";
    mkdir(folder.c_str(), 0777);
    const int fd = open(part.c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0666);
    if (fd < 0) {
        std::fprintf(stderr, "n64host: lockstep dump cannot write %s\n", part.c_str());
        std::_Exit(6);
    }
    for (uint32_t pg : dump_pages) {
        const uint64_t h = canonical(rdram, pg, page);
        uint32_t head[4] = {pg, 0, (uint32_t)h, (uint32_t)(h >> 32)};
        out(fd, head, sizeof head);
        out(fd, page, PAGE);
    }
    if (close(fd) != 0 || std::rename(part.c_str(), (folder + "/dump.bin").c_str()) != 0) std::_Exit(6);
    std::fprintf(stderr, "n64host: lockstep dump of %zu pages at poll %lld in %s\n", dump_pages.size(),
                 (long long)dump_poll, folder.c_str());
    std::fflush(stderr);
    std::_Exit(0);
}

int open_files() {
    const char *dir = std::getenv("N64R_LOCKSTEP_DIR");
    if (!dir || !*dir) return 0;
    if (const char *r = std::getenv("N64R_LOCKSTEP_RAM")) ram_bytes = (uint32_t)std::strtoul(r, nullptr, 0) & ~(PAGE - 1);
    if (const char *c = std::getenv("N64R_LOCKSTEP_CALLS")) max_calls = (uint32_t)std::strtoul(c, nullptr, 10);
    for (const char *w = std::getenv("N64R_LOCKSTEP_WATCH"); w && *w && watches.size() < 128;) {
        char *end;
        unsigned long a = std::strtoul(w, &end, 16);
        Watch x;
        if (*end == '@') {
            x.from = std::strtoull(end + 1, &end, 10);
            x.to = *end == '-' ? std::strtoull(end + 1, &end, 10) : x.from;
        }
        x.phys = (uint32_t)(a & 0x1FFFFFFFu);
        if ((!*end || *end == ',') && !(a & 3) && x.phys + 4 <= ram_bytes) watches.push_back(x);
        w = std::strchr(w, ',');
        w = w ? w + 1 : nullptr;
    }
    watching = !watches.empty();
    if (const char *c = std::getenv("N64R_LOCKSTEP_CALLS_POLLS"); c && *c) {
        char *end;
        window_from = std::strtoull(c, &end, 10);
        window_to = *end == '-' ? std::strtoull(end + 1, nullptr, 10) : window_from;
    }
    mkdir(dir, 0777);
    std::string d(dir);
    if (const char *spec = std::getenv("N64R_LOCKSTEP_DUMP"); spec && *spec) return open_dump(d, spec);
    log_fd = open((d + "/lockstep.jsonl").c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_APPEND | O_CLOEXEC, 0666);
    if (log_fd < 0 || !ram_bytes) {
        std::fprintf(stderr, "n64host: lockstep off (cannot write %s)\n", dir);
        return 0;
    }
    for (Pages *s : {&hit_pages, &poll_pages}) {
        s->last.assign(ram_bytes / PAGE, 0);
        s->shadow.assign(ram_bytes, 0);
    }
    char meta[256];
    std::snprintf(meta, sizeof meta, "{\"k\":\"meta\",\"side\":\"oracle\",\"ram\":%u,\"page\":%u,\"bytes\":\"canonical-be\","
                  "\"hash\":\"fnv1a64-le64\",\"calls\":%u,\"frame\":\"poll\",\"ring\":\"pow2\",\"window\":%u,\"ordinal\":\"thread\",\"totals\":\"poll\",\"watch\":\"%08x\",", ram_bytes, PAGE, max_calls, WINDOW,
                  watching ? 0x80000000u + watches[0].phys : 0u);
    std::string line(meta);
    line += "\"replaced\":[";  // the runtime replacements' vrams, from the table main.cpp registers (replaced_funcs.inl)
    for (size_t i = 0; uint32_t vram = n64host_replaced_vram(i); i++) {
        char v[16];
        std::snprintf(v, sizeof v, "%s\"%08x\"", i ? "," : "", vram);
        line += v;
    }
    line += "]}\n";
    out(log_fd, line.data(), line.size());
    return 1;
}

// The changed pages of one stream (lock held) as "[[page,hash],...]", hashed in canonical order.
std::string changed(Pages &s, const uint8_t *rdram) {
    static uint8_t page[PAGE];
    std::string list = "[";
    const char *sep = "";
    for (uint32_t pg = 0; pg < ram_bytes / PAGE; pg++) {
        // Only pages whose raw bytes changed since the last point are re-hashed: a point costs about one memcmp
        // of RDRAM, so the guest loses no retraces to the recorder.
        if (s.started && std::memcmp(s.shadow.data() + pg * PAGE, rdram + pg * PAGE, PAGE) == 0) continue;
        std::memcpy(s.shadow.data() + pg * PAGE, rdram + pg * PAGE, PAGE);
        const uint64_t h = canonical(s.shadow.data(), pg, page);
        if (s.started && s.last[pg] == h) continue;
        s.last[pg] = h;
        list += sep;
        list += "[" + std::to_string(pg) + "," + hex64(h) + "]";
        sep = ",";
    }
    s.started = true;
    return list + "]";
}

// One hit point (lock held).
void point(const char *name, const uint8_t *rdram, uint32_t sp) {
    std::string line = "{\"k\":\"hit\",\"n\":" + std::to_string(points++) + ",\"fn\":\"" + name + "\",\"thr\":" +
                       std::to_string(tid()) + ",\"vi\":" + std::to_string(n64host_golden_frame()) + ",\"sp\":" +
                       hex32(sp) + ",\"pages\":";
    out_line(line + changed(hit_pages, rdram) + "}\n");
}

void watch(const char *name, const uint8_t *rdram, const char *at) {
    for (Watch &x : watches) {
        uint32_t now = word(rdram, x.phys);
        if (now == x.value) continue;
        if (n64host_tas_poll >= x.from && n64host_tas_poll <= x.to && x.lines < 4000) {
            out_line(std::string("{\"k\":\"watch\",\"w\":") + hex32(0x80000000u + x.phys) + ",\"poll\":" +
                     std::to_string(n64host_tas_poll) + ",\"at\":\"" + at + "\",\"fn\":\"" + name + "\",\"thr\":" +
                     std::to_string(tid()) + ",\"old\":" + hex32(x.value) + ",\"new\":" + hex32(now) + "}\n");
            x.lines++;
        }
        x.value = now;
    }
}
}  // namespace

extern "C" {
int n64host_lockstep_on = open_files();
}

extern "C" void n64host_lockstep_entry(const char *name, const uint8_t *rdram, uint32_t a0, uint32_t a1, uint32_t a2,
                                       uint32_t a3, uint64_t f12, uint64_t f14, uint32_t sp) {
    if (dump_poll >= 0) return;  // the dump replay records nothing
    int64_t call = -1;
    {
        std::lock_guard<std::mutex> g(lock);
        Fn &f = fns[name];
        const char *caller = frames.empty() ? nullptr : frames.back().name;
        const long thr = tid();
        PairCount &pc = ordinals[{caller, name, thr}];
        const unsigned ordinal = pc.n++;
        if (ordinal == 0) {  // the pair's first call: its line names it once, the `tot` lines then use its id
            pc.id = (unsigned)pairs.size();
            pairs.push_back(&pc);
            out_line("{\"k\":\"pair\",\"id\":" + std::to_string(pc.id) + ",\"thr\":" + std::to_string(thr) +
                     ",\"caller\":\"" + (caller ? caller : "?") + "\",\"fn\":\"" + name + "\"}\n");
        }
        if (keeps(ordinal)) call = (int64_t)f.calls;
        f.calls++;
        if (!f.hit) {
            f.hit = true;
            point(name, rdram, sp);
        }
        if (call >= 0)
            out_line("{\"k\":\"call\",\"fn\":\"" + std::string(name) + "\",\"i\":" + std::to_string(call) +
                     ",\"caller\":\"" + (caller ? caller : "?") + "\",\"o\":" + std::to_string(ordinal) +
                     ",\"frame\":" + std::to_string(n64host_tas_poll) + ",\"thr\":" +
                     std::to_string(tid()) + ",\"sp\":" + hex32(sp) + ",\"a\":[" + hex32(a0) + "," + hex32(a1) + "," +
                     hex32(a2) + "," + hex32(a3) + "],\"f12\":" + hex64(f12) + ",\"f14\":" + hex64(f14) + "}\n");
        if (watching) watch(name, rdram, "enter");
    }
    frames.push_back({name, sp, call, {a0, a1, a2, a3}});
}

// sp is the restored stack pointer, equal to the one at entry; frames left above it were tail calls or skipped.
extern "C" void n64host_lockstep_return(const char *name, const uint8_t *rdram, uint32_t v0, uint32_t v1, uint64_t f0,
                                        uint32_t sp) {
    if (dump_poll >= 0) return;
    while (!frames.empty() && frames.back().sp < sp) frames.pop_back();
    if (frames.empty() || frames.back().sp != sp || frames.back().name != name) return;
    const Frame fr = frames.back();
    frames.pop_back();
    std::lock_guard<std::mutex> g(lock);
    if (fr.call >= 0)
        out_line("{\"k\":\"ret\",\"fn\":\"" + std::string(name) + "\",\"i\":" + std::to_string(fr.call) + ",\"thr\":" +
                 std::to_string(tid()) + ",\"r\":[" + hex32(v0) + "," + hex32(v1) + "],\"f0\":" + hex64(f0) +
                 ",\"m\":" + windows(rdram, fr.a) + "}\n");
    if (watching) watch(name, rdram, "ret");
}

// One controller poll of a TAS movie (the host's n64host_golden_pad, channel 0's read): a `poll` point keyed by the
// poll index, with the VI count, the virtual guest clock in ns, the pad word each controller answers this poll (buttons << 16 | stick x << 8 |
// stick y, as the guest's OSContPad reads it) and the pages changed since the previous poll.
extern "C" void n64host_lockstep_poll(uint64_t poll, uint64_t vi, uint64_t ns, const uint8_t *rdram,
                                      const uint32_t *pads, unsigned count) {
    std::lock_guard<std::mutex> g(lock);
    if (dump_poll >= 0) {  // the dump replay: only the dump poll does anything, and it ends the process
        if ((int64_t)poll == dump_poll) dump(rdram);
        return;
    }
    // The per-poll totals first: every pair whose count moved since the previous `tot` line, as [id, calls so far],
    // the calls made before this poll's event (the port's trace.c takes the same snapshot at its poll tick).
    std::string tot;
    for (PairCount *pc : pairs)
        if (pc->n != pc->told) {
            pc->told = pc->n;
            tot += (tot.empty() ? "[" : ",[") + std::to_string(pc->id) + "," + std::to_string(pc->n) + "]";
        }
    if (!tot.empty()) out_line("{\"k\":\"tot\",\"poll\":" + std::to_string(poll) + ",\"d\":[" + tot + "]}\n");
    std::string line = "{\"k\":\"poll\",\"poll\":" + std::to_string(poll) + ",\"vi\":" + std::to_string(vi) + ",\"ns\":" +
                       std::to_string(ns) + ",\"pad\":[";
    for (unsigned i = 0; i < count; i++) line += (i ? "," : "") + hex32(pads[i]);
    out_line(line + "],\"pages\":" + changed(poll_pages, rdram) + "}\n");
}
