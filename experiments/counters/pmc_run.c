// pmc_run: count CPU events for one run of a benchmark (docs/decisions.md D12).
//
//   sudo pmc_run EVENTS [-e KEY=VAL]... -- BINARY [ARGS]...
//
// EVENTS is a comma-separated list of kpep event names (see /usr/share/kpep/a15.plist
// on the M2). macOS lets only root program *and read* the PMU, while the benchmark
// must not run as root (this program is allowed through sudo without a password), so:
//   1. as root: program the counters on every core;
//   2. read all cores' counters, fork; the child drops to the invoking user
//      (SUDO_UID/SUDO_GID) and execs BINARY with only the -e variables added;
//   3. when it exits, read all cores again and print one "pmc EVENT DELTA" line per
//      event to stdout (after the child's own output); then release the counters.
// The deltas are system-wide: they include whatever else ran meanwhile. Callers
// measure that background with a control run (e.g. /bin/sleep for the same time).
// Nothing is written to disk, so root never touches a caller-chosen path.
#include <dlfcn.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/sysctl.h>
#include <sys/wait.h>
#include <unistd.h>

#include "kperf.h"

#define DECL(ret, name, args) static ret(*name) args;
KPERF_FUNCS(DECL)
KPEP_FUNCS(DECL)
static uint32_t (*kpc_get_counter_count)(uint32_t classes);
static int (*kpc_get_cpu_counters)(bool all_cpus, uint32_t classes, int *curcpu,
                                   uint64_t *buf);
static int forced;

static void release(void) {
  if (!forced) return;
  kpc_set_thread_counting(0);
  kpc_set_counting(0);
  kpc_force_all_ctrs_set(0);
}

static void die(const char *msg) {
  fprintf(stderr, "pmc_run: %s\n", msg);
  exit(2);  // atexit(release) gives the counters back
}

static void *sym(void *lib, const char *name) {
  void *p = dlsym(lib, name);
  if (!p) {
    fprintf(stderr, "pmc_run: missing symbol %s\n", name);
    exit(2);
  }
  return p;
}

static void load(void) {
  void *kperf = dlopen(KPERF_PATH, RTLD_LAZY);
  void *kpep = dlopen(KPEP_PATH, RTLD_LAZY);
  if (!kperf || !kpep) die("cannot load kperf frameworks");
#define LOAD_KPERF(ret, name, args) name = (ret(*) args)sym(kperf, #name);
#define LOAD_KPEP(ret, name, args) name = (ret(*) args)sym(kpep, #name);
  KPERF_FUNCS(LOAD_KPERF)
  KPEP_FUNCS(LOAD_KPEP)
  kpc_get_counter_count = (uint32_t(*)(uint32_t))sym(kperf, "kpc_get_counter_count");
  kpc_get_cpu_counters =
      (int (*)(bool, uint32_t, int *, uint64_t *))sym(kperf, "kpc_get_cpu_counters");
}

int main(int argc, char **argv) {
  if (argc < 4) die("usage: pmc_run EVENTS [-e KEY=VAL]... -- BINARY [ARGS]...");
  if (geteuid() != 0) die("must run as root (via sudo)");
  const char *uid_s = getenv("SUDO_UID"), *gid_s = getenv("SUDO_GID");
  if (!uid_s || !gid_s) die("SUDO_UID/SUDO_GID not set; run through sudo");
  uid_t uid = (uid_t)strtoul(uid_s, NULL, 10);
  gid_t gid = (gid_t)strtoul(gid_s, NULL, 10);
  if (uid == 0) die("refusing to run the benchmark as root");

  char *events = strdup(argv[1]);
  char *child_env[64];
  int n_env = 0, i = 2;
  for (; i < argc && strcmp(argv[i], "--") != 0; i++) {
    if (strcmp(argv[i], "-e") != 0 || i + 1 >= argc || !strchr(argv[i + 1], '='))
      die("expected -e KEY=VAL or --");
    if (n_env >= 63) die("too many -e");
    child_env[n_env++] = argv[++i];
  }
  if (i + 1 >= argc) die("no binary after --");
  char **child_argv = &argv[i + 1];

  load();
  atexit(release);
  if (kpc_force_all_ctrs_set(1)) die("kpc_force_all_ctrs_set failed");
  forced = 1;
  kpep_db *db;
  kpep_config *cfg;
  if (kpep_db_create(NULL, &db) || kpep_config_create(db, &cfg) ||
      kpep_config_force_counters(cfg))
    die("cannot open the PMU event database");
  char names[KPC_MAX_COUNTERS][96];
  int n_ev = 0;
  for (char *tok = strtok(events, ","); tok; tok = strtok(NULL, ",")) {
    if (n_ev >= KPC_MAX_COUNTERS) die("too many events");
    kpep_event *ev;
    uint32_t err = 0;
    if (kpep_db_event(db, tok, &ev)) {
      fprintf(stderr, "pmc_run: unknown event %s\n", tok);
      exit(2);
    }
    if (kpep_config_add_event(cfg, &ev, 0, &err)) {
      fprintf(stderr, "pmc_run: cannot add %s (counter conflict or too many events)\n", tok);
      exit(2);
    }
    snprintf(names[n_ev++], sizeof names[0], "%s", tok);
  }
  uint32_t classes = 0;
  size_t count = 0, map[KPC_MAX_COUNTERS] = {0};
  kpc_config_t regs[KPC_MAX_COUNTERS] = {0};
  if (kpep_config_kpc_classes(cfg, &classes) || kpep_config_kpc_count(cfg, &count) ||
      kpep_config_kpc_map(cfg, map, sizeof map) || kpep_config_kpc(cfg, regs, sizeof regs))
    die("cannot build the counter configuration");
  if (kpc_set_config(classes, regs) || kpc_set_counting(classes))
    die("cannot program the counters");

  int ncpu = 0;
  size_t len = sizeof ncpu;
  if (sysctlbyname("hw.ncpu", &ncpu, &len, NULL, 0) || ncpu <= 0) die("hw.ncpu");
  uint32_t per_cpu = kpc_get_counter_count(classes);
  uint64_t *before = calloc((size_t)ncpu * per_cpu, sizeof(uint64_t));
  uint64_t *after = calloc((size_t)ncpu * per_cpu, sizeof(uint64_t));
  int cur;
  if (!before || !after) die("out of memory");

  fflush(NULL);
  if (kpc_get_cpu_counters(true, classes, &cur, before)) die("cannot read counters");
  pid_t pid = fork();
  if (pid < 0) die("fork failed");
  if (pid == 0) {
    if (setgid(gid) || setuid(uid) || setuid(0) == 0) {
      fprintf(stderr, "pmc_run: cannot drop privileges\n");
      _exit(126);
    }
    for (int e = 0; e < n_env; e++) putenv(child_env[e]);
    execvp(child_argv[0], child_argv);
    fprintf(stderr, "pmc_run: exec %s: %s\n", child_argv[0], strerror(errno));
    _exit(127);
  }
  int status = 0;
  while (waitpid(pid, &status, 0) < 0 && errno == EINTR) {
  }
  if (kpc_get_cpu_counters(true, classes, &cur, after)) die("cannot read counters");

  for (int e = 0; e < n_ev; e++) {
    unsigned long long total = 0;
    for (int c = 0; c < ncpu; c++)
      total += after[(size_t)c * per_cpu + map[e]] - before[(size_t)c * per_cpu + map[e]];
    printf("pmc %s %llu\n", names[e], total);
  }
  return WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
}
