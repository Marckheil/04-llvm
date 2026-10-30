"""Run a binary under the alloc probe; print time and C4-sum distance. argv: label binary [ENV=VAL...]"""
import os, re, subprocess, sys
lab, binary, *envs = sys.argv[1:]
env = dict(os.environ, DYLD_INSERT_LIBRARIES=os.path.join(os.path.dirname(__file__), "alloc_probe.dylib"))
env.update(e.split("=", 1) for e in envs)
p = subprocess.run([binary], env=env, capture_output=True, text=True)
a = {int(m[1]): int(m[3], 16) for m in re.finditer(r"ALLOC (\d+) (\d+) (0x[0-9a-f]+)", p.stderr)}
d = a[6] - a[5]
print(f"{lab:<30} {p.stdout.strip()}  C4-sum={'-' if d < 0 else '+'}{hex(abs(d))}", flush=True)
