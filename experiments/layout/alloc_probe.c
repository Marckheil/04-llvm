// Interposes posix_memalign/free. Logs every allocation to stderr as
//   ALLOC <idx> <size> <addr>
// and, if ALLOC_SHIFT_<idx>=<bytes> is set, returns the block shifted by that
// many bytes (the original pointer is remembered so free() still works).
// If ALLOC_PAIR=<sum_idx>:<c4_idx>:<dist> is set, the two blocks are carved from
// one 16 MB-aligned arena: first at arena+0x4000, second at first+dist.
//
// Only sees allocations in binaries linked without benchmarks/runtime/placement.c
// (set target.runtime: [] in the config); that runtime replaces posix_memalign.
// Build: clang -O2 -dynamiclib alloc_probe.c -o alloc_probe.dylib (next to runlog.py).
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>

#define DYLD_INTERPOSE(_new, _old) \
  __attribute__((used)) static struct { const void *n, *o; } _interpose_##_old \
  __attribute__((section("__DATA,__interpose"))) = { (const void *)&_new, (const void *)&_old };

#define MAXA 64
static void *shifted[MAXA], *orig[MAXA];
static int nshift, idx;
static char *pair_second;

static int pair_alloc(void **out, size_t size) {
  const char *e = getenv("ALLOC_PAIR");
  if (!e) return -1;
  int i1, i2; long dist;
  if (sscanf(e, "%d:%d:%li", &i1, &i2, &dist) != 3) return -1;
  if (idx == i1) {
    void *arena;
    size_t span = 0x4000 + (size_t)dist + (1 << 20) + size;
    if (posix_memalign(&arena, 1 << 24, span)) return -1;
    *out = (char *)arena + 0x4000;
    pair_second = (char *)*out + dist;
    if (nshift < MAXA) { shifted[nshift] = *out; orig[nshift++] = NULL; }
    return 0;
  }
  if (idx == i2 && pair_second) {
    *out = pair_second;
    if (nshift < MAXA) { shifted[nshift] = *out; orig[nshift++] = NULL; }
    return 0;
  }
  return -1;
}

static int probe_memalign(void **out, size_t align, size_t size) {
  char name[32];
  snprintf(name, sizeof name, "ALLOC_SHIFT_%d", idx);
  const char *e = getenv(name);
  size_t shift = e ? strtoul(e, NULL, 0) : 0;
  if (pair_alloc(out, size) == 0) {
    fprintf(stderr, "ALLOC %d %zu 0x%lx\n", idx++, size, (unsigned long)(uintptr_t)*out);
    return 0;
  }
  void *p;
  int err = posix_memalign(&p, align, size + shift);
  if (err) return err;
  *out = (char *)p + shift;
  if (shift && nshift < MAXA) { shifted[nshift] = *out; orig[nshift++] = p; }
  fprintf(stderr, "ALLOC %d %zu 0x%lx\n", idx++, size, (unsigned long)(uintptr_t)*out);
  return 0;
}

static void probe_free(void *p) {
  for (int i = 0; i < nshift; i++)
    if (shifted[i] == p) { p = orig[i]; shifted[i] = NULL; break; }
  if (p) free(p);  // NULL orig = arena-owned block, intentionally leaked
}

DYLD_INTERPOSE(probe_memalign, posix_memalign)
DYLD_INTERPOSE(probe_free, free)
