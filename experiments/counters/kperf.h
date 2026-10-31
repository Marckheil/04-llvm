// Declarations for Apple's private kperf / kperfdata frameworks (macOS 11+, Apple
// Silicon). They are not in any SDK; the signatures follow the reverse-engineered
// descriptions in public PMU tooling. Both frameworks are loaded with dlopen.
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define KPC_CLASS_FIXED_MASK 1u
#define KPC_CLASS_CONFIGURABLE_MASK 2u
#define KPC_MAX_COUNTERS 32

typedef uint64_t kpc_config_t;
typedef struct kpep_db kpep_db;
typedef struct kpep_config kpep_config;
typedef struct kpep_event kpep_event;

#define KPERF_FUNCS(X)                                                     \
  X(int, kpc_force_all_ctrs_set, (int val))                                \
  X(int, kpc_set_config, (uint32_t classes, kpc_config_t *config))         \
  X(int, kpc_set_counting, (uint32_t classes))                             \
  X(int, kpc_set_thread_counting, (uint32_t classes))                      \
  X(int, kpc_get_thread_counters, (uint32_t tid, uint32_t n, uint64_t *buf))

#define KPEP_FUNCS(X)                                                        \
  X(int, kpep_db_create, (const char *name, kpep_db **db))                   \
  X(int, kpep_db_event, (kpep_db * db, const char *name, kpep_event **ev))   \
  X(int, kpep_config_create, (kpep_db * db, kpep_config **cfg))              \
  X(int, kpep_config_force_counters, (kpep_config * cfg))                    \
  X(int, kpep_config_add_event,                                              \
    (kpep_config * cfg, kpep_event **ev, uint32_t flag, uint32_t *err))      \
  X(int, kpep_config_kpc_classes, (kpep_config * cfg, uint32_t *classes))    \
  X(int, kpep_config_kpc_count, (kpep_config * cfg, size_t *count))          \
  X(int, kpep_config_kpc_map, (kpep_config * cfg, size_t *buf, size_t size)) \
  X(int, kpep_config_kpc, (kpep_config * cfg, kpc_config_t *buf, size_t size))

#define KPERF_PATH "/System/Library/PrivateFrameworks/kperf.framework/kperf"
#define KPEP_PATH "/System/Library/PrivateFrameworks/kperfdata.framework/kperfdata"
