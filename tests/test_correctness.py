import numpy as np

from llvm04.bench.correctness import MISCOMPILE, PASS, compare

F64 = np.dtype(np.float64)
I32 = np.dtype(np.int32)


def f(*xs):
    return np.array(xs, F64).tobytes()


def test_bitwise_identical_passes():
    r = compare([f(1, 2), f(3)], [f(1, 2), f(3)], F64, 1e-9)
    assert r.status == PASS and r.bitwise


def test_last_bit_drift_passes():
    x = 1.0 + 2 ** -52
    r = compare([f(1.0, 2.0)], [f(x, 2.0)], F64, 1e-9)
    assert r.ok and not r.bitwise and 0 < r.max_rel_err < 1e-15


def test_error_scaled_by_largest_magnitude():
    # 1e-8 absolute on an element of 1e-3 is small next to max|ref| = 1e3.
    r = compare([f(1e3, 1e-3)], [f(1e3, 1e-3 + 1e-8)], F64, 1e-9)
    assert r.ok
    assert not compare([f(1e3, 1e-3)], [f(1e3, 1e-3 + 1e-5)], F64, 1e-9).ok


def test_beyond_tolerance_is_miscompile():
    r = compare([f(4.4925)], [f(4.49250004925)], F64, 1e-9)
    assert r.status == MISCOMPILE and "array 0" in r.detail


def test_integer_data_must_match_exactly():
    a, b = np.array([1, 2], I32).tobytes(), np.array([1, 3], I32).tobytes()
    assert compare([a], [b], I32, 1e-9).status == MISCOMPILE


def test_nan_positions_must_match():
    assert compare([f(np.nan, 1)], [f(np.nan, 1)], F64, 1e-9).ok
    assert compare([f(np.nan, 1)], [f(0.0, 1)], F64, 1e-9).status == MISCOMPILE


def test_shape_changes_are_miscompiles():
    assert compare([f(1)], [f(1), f(2)], F64, 1e-9).status == MISCOMPILE
    assert compare([f(1, 2)], [f(1)], F64, 1e-9).status == MISCOMPILE
