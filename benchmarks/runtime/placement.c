// Deterministic array placement for PolyBench (docs/decisions.md D7).
//
// Linked as a separate object into every benchmark binary, so it overrides
// posix_memalign/free for calls from the executable (PolyBench's xmalloc) and
// stays outside the bitcode that candidate pipelines optimize.
//
// macOS malloc picks addresses that differ between otherwise identical
// binaries; on the M2 some relative placements cost doitgen ~20%. Here every
// block comes from one 16 MB-aligned arena in allocation order, so the relative
// layout depends only on the program, never on the binary's bytes.
//
// LLVM04_LAYOUT_SALT=<k> moves block i by k * 3 * (i + 1) pages. Re-timing a
// result with a few salts shows whether it depends on layout.
#include <malloc/malloc.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>

#define ARENA_ALIGN ((size_t)16 << 20)
#define ARENA_RESERVE ((size_t)8 << 30)  // virtual only; pages are touched lazily
#define PAGE 4096

static char *arena_base, *arena_next, *arena_end;
static unsigned block_idx;
static size_t salt;

static void arena_init(void) {
  char *p = mmap(NULL, ARENA_RESERVE + ARENA_ALIGN, PROT_READ | PROT_WRITE,
                 MAP_PRIVATE | MAP_ANON, -1, 0);
  if (p == MAP_FAILED) {
    fprintf(stderr, "[placement] cannot reserve arena\n");
    abort();
  }
  arena_base = (char *)(((uintptr_t)p + ARENA_ALIGN - 1) & ~(ARENA_ALIGN - 1));
  arena_next = arena_base;
  arena_end = arena_base + ARENA_RESERVE;
  const char *s = getenv("LLVM04_LAYOUT_SALT");
  salt = s ? strtoul(s, NULL, 10) : 0;
}

int posix_memalign(void **out, size_t align, size_t size) {
  if (!arena_base) arena_init();
  if (align < PAGE) align = PAGE;
  uintptr_t start = (uintptr_t)arena_next + salt * 3 * (block_idx + 1) * PAGE;
  start = (start + align - 1) & ~(uintptr_t)(align - 1);
  if (start + size > (uintptr_t)arena_end) {
    fprintf(stderr, "[placement] arena exhausted\n");
    abort();
  }
  *out = (void *)start;
  arena_next = (char *)start + size + PAGE;  // one guard page between blocks
  block_idx++;
  return 0;
}

void free(void *p) {
  if (!p) return;
  if ((char *)p >= arena_base && (char *)p < arena_end) return;  // arena is never reused
  malloc_zone_t *zone = malloc_zone_from_ptr(p);
  if (zone) malloc_zone_free(zone, p);
}
