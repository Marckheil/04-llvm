"""Wrappers around the pinned LLVM tools: bitcode -> opt -> llc -> link."""

from dataclasses import dataclass
from pathlib import Path

from passpilot import proc
from passpilot.config import sdk_path


class CompileError(Exception):
    def __init__(self, stage: str, cmd: list, stderr: str):
        super().__init__(f"{stage} failed: {stderr.strip()[:500]}")
        self.stage = stage
        self.cmd = cmd
        self.stderr = stderr


@dataclass
class BuildResult:
    binary: Path
    opt_s: float
    llc_s: float
    link_s: float
    binary_size: int

    @property
    def compile_s(self) -> float:
        """Middle-end + backend time; linking is excluded (identical for all candidates)."""
        return self.opt_s + self.llc_s


class Toolchain:
    def __init__(self, cfg: dict):
        self.bin_dir = Path(cfg["llvm"]["bin_dir"])
        self.version = cfg["llvm"]["version"]
        self.codegen_opt = cfg["target"]["codegen_opt"]
        self.link_flags = cfg["target"]["link_flags"]
        self.timeout_s = cfg["limits"]["compile_timeout_s"]

    def tool(self, name: str) -> Path:
        path = self.bin_dir / name
        if not path.exists():
            raise FileNotFoundError(f"{name} not found in pinned LLVM dir {self.bin_dir}")
        return path

    def check_version(self) -> None:
        out = proc.run([self.tool("opt"), "--version"], self.timeout_s).stdout
        if f"version {self.version}" not in out:
            raise RuntimeError(f"expected LLVM {self.version}, got:\n{out}")

    def _run(self, stage: str, cmd: list) -> proc.ProcResult:
        res = proc.run(cmd, self.timeout_s)
        if res.returncode != 0:
            raise CompileError(stage, cmd, res.stderr)
        return res

    def clang(self, args: list) -> proc.ProcResult:
        return self._run("clang", [self.tool("clang"), "-isysroot", sdk_path(), *args])

    def llvm_link(self, inputs: list[Path], out: Path) -> proc.ProcResult:
        return self._run("llvm-link", [self.tool("llvm-link"), *inputs, "-o", out])

    def opt(self, in_bc: Path, out_bc: Path, pipeline: str) -> proc.ProcResult:
        return self._run("opt", [self.tool("opt"), f"-passes={pipeline}", in_bc, "-o", out_bc])

    def llc(self, in_bc: Path, out_obj: Path) -> proc.ProcResult:
        return self._run(
            "llc", [self.tool("llc"), self.codegen_opt, "-filetype=obj", in_bc, "-o", out_obj]
        )

    def link(self, objs: list[Path], out_bin: Path) -> proc.ProcResult:
        return self.clang([*objs, *self.link_flags, "-o", out_bin])

    def build(self, in_bc: Path, pipeline: str, out_bin: Path) -> BuildResult:
        """Apply `pipeline` to `in_bc` and produce a linked binary at `out_bin`."""
        out_bin.parent.mkdir(parents=True, exist_ok=True)
        # Append, don't use with_suffix: "gemm.cand1" would become "gemm.o" and collide.
        opt_bc = out_bin.with_name(out_bin.name + ".opt.bc")
        obj = out_bin.with_name(out_bin.name + ".o")
        opt_res = self.opt(in_bc, opt_bc, pipeline)
        llc_res = self.llc(opt_bc, obj)
        link_res = self.link([obj], out_bin)
        return BuildResult(
            binary=out_bin,
            opt_s=opt_res.elapsed_s,
            llc_s=llc_res.elapsed_s,
            link_s=link_res.elapsed_s,
            binary_size=out_bin.stat().st_size,
        )
