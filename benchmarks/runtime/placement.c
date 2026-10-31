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
//
// LLVM04_DUMP_DIR=<dir> writes every block's final bytes to <dir>/block<i>.bin at
// exit. The correctness gate (llvm04/bench/correctness.py) compares them at full
// precision, which is far stricter than PolyBench's 2-decimal array dump.
#include <malloc/malloc.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <unistd.h>

#define ARENA_ALIGN ((size_t)16 << 20)
#define ARENA_RESERVE ((size_t)8 << 30)  // virtual only; pages are touched lazily
#define PAGE 4096

static char *arena_base, *arena_next, *arena_end;
static unsigned block_idx;
static size_t salt;

#define MAX_BLOCKS 64
static struct { const unsigned char *p; size_t n; } blocks[MAX_BLOCKS];

static void dump_blocks(void) {
  const char *dir = getenv("LLVM04_DUMP_DIR");
  if (!dir) return;
  for (unsigned i = 0; i < block_idx && i < MAX_BLOCKS; i++) {
    char path[4096];
    snprintf(path, sizeof path, "%s/block%u.bin", dir, i);
    FILE *f = fopen(path, "wb");
    if (!f || fwrite(blocks[i].p, 1, blocks[i].n, f) != blocks[i].n) {
      fprintf(stderr, "[placement] cannot write %s\n", path);
      _exit(3);
    }
    fclose(f);
  }
}

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
  atexit(dump_blocks);
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
  if (block_idx < MAX_BLOCKS) {
    blocks[block_idx].p = (const unsigned char *)start;
    blocks[block_idx].n = size;
  }
  arena_next = (char *)start + size + PAGE;  // one guard page between blocks
  block_idx++;
  return 0;
}

void free(void *p) {
  if (!p) return;
  // Arena blocks are never reused, so their final contents survive for the dump.
  if ((char *)p >= arena_base && (char *)p < arena_end) return;
  malloc_zone_t *zone = malloc_zone_from_ptr(p);
  if (zone) malloc_zone_free(zone, p);
}
