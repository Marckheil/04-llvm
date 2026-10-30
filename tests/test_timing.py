import pytest

from passpilot.bench.timing import RunError, cv, parse_polybench_time, speedup_ci


def test_parse_polybench_time_takes_last_line():
    assert parse_polybench_time("noise\n0.123456\n\n") == pytest.approx(0.123456)


def test_parse_polybench_time_empty():
    with pytest.raises(RunError):
        parse_polybench_time("")


def test_cv_zero_for_constant():
    assert cv([1.0, 1.0, 1.0]) == 0.0


def test_speedup_identical_not_significant():
    xs = [1.0, 1.01, 0.99, 1.02, 0.98] * 3
    s = speedup_ci(xs, xs, n_boot=2000)
    assert s.ratio == pytest.approx(1.0)
    assert not s.significant


def test_speedup_clear_win_is_significant():
    base = [1.0, 1.01, 0.99, 1.0, 1.02] * 3
    cand = [0.8, 0.81, 0.79, 0.8, 0.82] * 3
    s = speedup_ci(base, cand, n_boot=2000)
    assert s.ratio == pytest.approx(1.25, rel=0.02)
    assert s.significant and s.ci_lo > 1.0
