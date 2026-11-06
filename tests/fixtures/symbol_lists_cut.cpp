// a cut of src/symbol_lists.cpp of the N64Recomp pinned in hosts/n64recomp/PINS: the same three blocks, a slice of each
const std::unordered_set<std::string> N64Recomp::reimplemented_funcs {
    // Initialization functions
    "__osInitialize_common",
    "osInitialize",
    // Thread functions
    "osCreateThread",
    "osGetThreadId",
    // interrupt functions
    "osSetIntMask",
    "__osDisableInt",
    // Controller functions
    "osContInit",
    "osContStartReadData",
    // PI functions
    "osPiStartDma",
};

const std::unordered_set<std::string> N64Recomp::ignored_funcs {
    "osInitialize",
    "osSetIntMask",
    "osMapTLB",
    "__osSiDeviceBusy",
    "__osDispatchThread",
    "osContInit",
    "__osPiRawStartDma",
};

const std::unordered_set<std::string> N64Recomp::renamed_funcs {
    "lldiv",
};
