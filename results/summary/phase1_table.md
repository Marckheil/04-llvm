# Phase 1 results

Speedups are vs `default<O3>` (higher is better). Random-search columns: median confirmed speedup over seeds, then verdict counts. WIN = faster than O3 with a bootstrap CI above 1 *and* under every layout salt.

| benchmark | O3_s | O1_vs_O3 | O2_vs_O3 | Os_vs_O3 | o3_suffix_median_speedup | o3_suffix_verdicts | scratch_median_speedup | scratch_verdicts |
|---|---|---|---|---|---|---|---|---|
| 2mm | 1.858 | 0.993 | 0.993 | 0.996 | 0.998 | TIE:3 | 1.004 | TIE:2 WIN:1 |
| 3mm | 3.087 | 0.997 | 0.998 | 0.994 | 1.000 | TIE:3 | 1.001 | TIE:3 |
| adi | 6.367 | 0.996 | 0.999 | 1.000 | 1.000 | TIE:3 | 0.866 | SLOWER:3 |
| cholesky | 1.498 | 1.001 | 0.993 | 0.999 | 1.000 | TIE:3 | 0.999 | TIE:3 |
| correlation | 1.213 | 0.994 | 0.998 | 0.999 | 0.999 | LAYOUT_ONLY:1 TIE:2 | 0.997 | TIE:3 |
| covariance | 1.213 | 0.998 | 0.998 | 0.999 | 1.000 | TIE:3 | 1.001 | TIE:3 |
| deriche | 0.524 | 0.968 | 1.002 | 1.005 | 1.007 | LAYOUT_ONLY:1 TIE:2 | 0.976 | SLOWER:2 TIE:1 |
| doitgen | 4.072 | 0.999 | 0.992 | 0.995 | 0.998 | TIE:3 | 1.000 | TIE:3 |
| fdtd-2d | 0.597 | 0.585 | 0.997 | 0.997 | 1.002 | TIE:3 | 0.997 | SLOWER:1 TIE:2 |
| floyd-warshall | 9.092 | 1.006 | 0.997 | 1.012 | 0.998 | TIE:3 | 1.009 | WIN:3 |
| gemm | 1.753 | 0.411 | 0.996 | 1.000 | 0.998 | TIE:3 | 0.998 | SLOWER:1 TIE:2 |
| gramschmidt | 1.581 | 1.000 | 0.998 | 0.999 | 1.002 | TIE:3 | 1.001 | TIE:3 |
| heat-3d | 0.878 | 0.575 | 0.999 | 0.994 | 1.002 | TIE:3 | 0.998 | SLOWER:2 TIE:1 |
| jacobi-2d | 0.663 | 0.661 | 1.002 | 1.002 | 1.242 | WIN:3 | 1.239 | WIN:3 |
| lu | 2.969 | 0.999 | 1.000 | 1.000 | 1.000 | TIE:3 | 0.998 | SLOWER:1 TIE:2 |
| ludcmp | 2.940 | 1.001 | 1.002 | 1.003 | 0.998 | TIE:3 | 1.011 | TIE:1 WIN:2 |
| nussinov | 1.291 | 0.994 | 0.997 | 0.995 | 0.999 | TIE:3 | 0.953 | NO_CANDIDATE:1 SLOWER:2 |
| seidel-2d | 15.918 | 1.000 | 0.999 | 0.999 | 1.000 | TIE:3 | 0.797 | SLOWER:3 |
| symm | 0.875 | 1.008 | 1.006 | 1.008 | 1.000 | TIE:3 | 1.002 | LAYOUT_ONLY:1 TIE:2 |
| syr2k | 0.988 | 0.998 | 1.001 | 1.003 | 0.997 | TIE:3 | 1.176 | WIN:3 |
| syrk | 6.477 | 1.003 | 1.003 | 1.003 | 1.001 | TIE:2 WIN:1 | 1.025 | WIN:3 |
| trmm | 0.657 | 0.999 | 0.998 | 0.999 | 1.000 | TIE:3 | 0.998 | TIE:3 |

**Geomean vs O3:** O1 0.895x, O2 0.999x, Os 1.000x, o3_suffix 1.010x, scratch 0.999x

**o3_suffix:** 66 benchmark x seed runs; verdicts {'TIE': 60, 'LAYOUT_ONLY': 2, 'WIN': 4}; candidates {'OK': 1980}

**scratch:** 66 benchmark x seed runs; verdicts {'WIN': 15, 'TIE': 34, 'SLOWER': 15, 'NO_CANDIDATE': 1, 'LAYOUT_ONLY': 1}; candidates {'slow': 925, 'OK': 952, 'COMPILE_ERROR': 103}
