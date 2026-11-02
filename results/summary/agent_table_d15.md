# Phase 3 results

Median confirmed speedup vs `default<O3>` over seeds, then verdict counts, per arm. Random-search arms: o3_suffix=random_search_o3_suffix, scratch=random_search_scratch_d15. All arms: 30-compile budget, the same confirmation protocol (o4_llvm/search/confirm.py).

| benchmark | o3_suffix_median_speedup | o3_suffix_verdicts | scratch_median_speedup | scratch_verdicts | opus-5.5_median_speedup | opus-5.5_verdicts | haiku-5.5_median_speedup | haiku-5.5_verdicts | opus-5.5_full_median_speedup | opus-5.5_full_verdicts | haiku-5.5_full_median_speedup | haiku-5.5_full_verdicts |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2mm | 0.998 | TIE:3 | 1.000 | TIE:3 | - |  | 1.001 | TIE:3 | - |  | - |  |
| 3mm | 1.000 | TIE:3 | 1.000 | TIE:3 | - |  | 1.000 | TIE:3 | - |  | - |  |
| adi | 1.000 | TIE:3 | 0.862 | SLOWER:3 | 1.001 | TIE:3 | 0.998 | TIE:3 | - |  | - |  |
| cholesky | 1.000 | TIE:3 | 0.999 | TIE:3 | - |  | 1.000 | TIE:3 | - |  | - |  |
| correlation | 0.999 | LAYOUT_ONLY:1 TIE:2 | 0.999 | TIE:3 | - |  | 1.000 | TIE:3 | - |  | - |  |
| covariance | 1.000 | TIE:3 | 0.999 | TIE:3 | - |  | 1.001 | TIE:2 WIN:1 | - |  | - |  |
| deriche | 1.007 | LAYOUT_ONLY:1 TIE:2 | 0.964 | SLOWER:2 TIE:1 | - |  | 1.007 | LAYOUT_ONLY:1 TIE:2 | - |  | - |  |
| doitgen | 0.998 | TIE:3 | 1.001 | TIE:3 | - |  | 0.998 | TIE:3 | - |  | - |  |
| fdtd-2d | 1.002 | TIE:3 | 0.995 | SLOWER:2 TIE:1 | - |  | 1.004 | TIE:3 | - |  | - |  |
| floyd-warshall | 0.998 | TIE:3 | 1.011 | WIN:3 | 1.144 | WIN:3 | 1.009 | WIN:3 | 1.086 | WIN:2 | 1.011 | WIN:3 |
| gemm | 0.998 | TIE:3 | 0.994 | SLOWER:1 TIE:2 | 1.003 | TIE:3 | 1.004 | TIE:3 | - |  | - |  |
| gramschmidt | 1.002 | TIE:3 | 0.999 | TIE:3 | - |  | 1.001 | TIE:3 | - |  | - |  |
| heat-3d | 1.002 | TIE:3 | 1.000 | SLOWER:1 TIE:2 | 1.000 | SLOWER:1 TIE:2 | 0.998 | TIE:3 | - |  | - |  |
| jacobi-2d | 1.242 | WIN:3 | 1.234 | WIN:3 | 1.003 | TIE:2 WIN:1 | 1.248 | WIN:3 | 1.242 | WIN:3 | 1.246 | WIN:3 |
| lu | 1.000 | TIE:3 | 1.000 | SLOWER:1 TIE:2 | - |  | 1.001 | LAYOUT_ONLY:1 TIE:2 | - |  | - |  |
| ludcmp | 0.998 | TIE:3 | 1.010 | LAYOUT_ONLY:1 WIN:2 | - |  | 1.000 | TIE:3 | - |  | - |  |
| nussinov | 0.999 | TIE:3 | 0.958 | NO_CANDIDATE:1 SLOWER:2 | - |  | 0.996 | TIE:3 | - |  | - |  |
| seidel-2d | 1.000 | TIE:3 | 0.800 | SLOWER:2 TIE:1 | - |  | 1.002 | TIE:3 | - |  | - |  |
| symm | 1.000 | TIE:3 | 1.001 | TIE:3 | - |  | 0.998 | TIE:3 | - |  | - |  |
| syr2k | 0.997 | TIE:3 | 1.179 | WIN:3 | 1.000 | TIE:3 | 1.004 | TIE:3 | - |  | - |  |
| syrk | 1.001 | TIE:2 WIN:1 | 1.025 | WIN:3 | - |  | 0.997 | TIE:3 | - |  | - |  |
| trmm | 1.000 | TIE:3 | 0.999 | SLOWER:1 TIE:2 | - |  | 1.001 | TIE:3 | - |  | - |  |

## Agent arms

| arm | episodes | benchmarks | spend | WIN | material WIN (>= 1.03x) | $ per material WIN | compiles used (mean) | malformed calls | plain-O3 submissions | A/A false alarms | largest A/A effect |
|---|---|---|---|---|---|---|---|---|---|---|---|
| opus-5.5 | 18 | 6 | $12.51 | 4 | 3 | $4.17 | 11.4 / 30 | 8 / 589 (1.4%) | 14 | 1 (heat-3d SLOWER) | 1.0% |
  - opus-5.5 material WIN: floyd-warshall seed 0 1.144x [1.139, 1.146], worst layout 1.134x: `none` + `sroa early-cse simplifycfg instcombine loop-simplify lcssa loop-rotate licm indvars gvn instcombine dse licm loop-unroll instcombine simplifycfg`
  - opus-5.5 material WIN: floyd-warshall seed 2 1.167x [1.153, 1.169], worst layout 1.166x: `none` + `sroa early-cse<memssa> simplifycfg function-attrs loop-simplify loop-rotate licm loop-deletion gvn sccp correlated-propagation dse licm adce simplifycfg loop-unroll instcombine licm simplifycfg instcombine`
  - opus-5.5 material WIN: jacobi-2d seed 1 1.236x [1.231, 1.249], worst layout 1.212x: `O3` + `separate-const-offset-from-gep early-cse nary-reassociate early-cse`
  - stops {'submitted': 18}; final build source {'submitted': 18}
| haiku-5.5 | 66 | 22 | $7.26 | 7 | 3 | $2.42 | 12.6 / 30 | 25 / 2568 (1.0%) | 60 | 3 (covariance WIN, deriche LAYOUT_ONLY, lu LAYOUT_ONLY) | 2.3% |
  - haiku-5.5 material WIN: jacobi-2d seed 0 1.248x [1.240, 1.250], worst layout 1.223x: `none` + `sroa early-cse loop-simplify lcssa loop-rotate licm indvars loop-vectorize loop-unroll instcombine simplifycfg gvn dce`
  - haiku-5.5 material WIN: jacobi-2d seed 1 1.260x [1.241, 1.267], worst layout 1.226x: `none` + `sroa early-cse loop-simplify lcssa loop-rotate licm indvars loop-unroll-and-jam loop-vectorize instcombine simplifycfg gvn dce`
  - haiku-5.5 material WIN: jacobi-2d seed 2 1.248x [1.237, 1.252], worst layout 1.218x: `none` + `mem2reg sroa early-cse loop-simplify lcssa loop-rotate indvars loop-vectorize instcombine simplifycfg gvn dce`
  - stops {'submitted': 66}; final build source {'submitted': 66}
| opus-5.5_full | 5 | 2 | $6.32 | 5 | 4 | $1.58 | 29.0 / 30 | 4 / 291 (1.4%) | 0 | 0 (none) | 0.0% |
  - opus-5.5_full material WIN: jacobi-2d seed 0 1.239x [1.221, 1.266], worst layout 1.219x: `O3` + `separate-const-offset-from-gep`
  - opus-5.5_full material WIN: jacobi-2d seed 1 1.243x [1.235, 1.252], worst layout 1.215x: `O3` + `separate-const-offset-from-gep early-cse nary-reassociate gvn dce`
  - opus-5.5_full material WIN: jacobi-2d seed 2 1.242x [1.233, 1.254], worst layout 1.217x: `O3` + `separate-const-offset-from-gep`
  - opus-5.5_full material WIN: floyd-warshall seed 0 1.161x [1.156, 1.163], worst layout 1.155x: `none` + `sroa early-cse<memssa> instcombine simplifycfg loop-simplify loop-rotate licm instcombine simplifycfg loop-unroll instcombine simplifycfg`
  - stops {'submitted': 4, 'max_usd': 1}; final build source {'submitted': 4, 'history_best': 1}
| haiku-5.5_full | 6 | 2 | $1.00 | 6 | 4 | $0.25 | 30.0 / 30 | 4 / 377 (1.1%) | 0 | 0 (none) | 0.0% |
  - haiku-5.5_full material WIN: jacobi-2d seed 0 1.291x [1.228, 1.345], worst layout 1.226x: `O3` + `separate-const-offset-from-gep`
  - haiku-5.5_full material WIN: jacobi-2d seed 1 1.246x [1.240, 1.252], worst layout 1.222x: `none` + `mem2reg loop-simplify lcssa separate-const-offset-from-gep nary-reassociate loop-rotate licm loop-vectorize instcombine loop-unroll gvn dce`
  - haiku-5.5_full material WIN: jacobi-2d seed 2 1.243x [1.229, 1.271], worst layout 1.221x: `O3` + `separate-const-offset-from-gep`
  - haiku-5.5_full material WIN: floyd-warshall seed 0 1.168x [1.151, 1.177], worst layout 1.163x: `none` + `sroa early-cse loop-simplify lcssa loop-rotate licm instcombine simplifycfg loop-unroll instcombine simplifycfg`
  - stops {'submitted': 6}; final build source {'submitted': 6}
