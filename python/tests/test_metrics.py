"""Layer 1 Unit Tests: MetricsCalculator (black-box + white-box)"""
import pytest
from emu.metrics import MetricsCalculator


class TestPercentile:
    """Black-box: correct output for known inputs."""

    def test_percentile_p50_even(self):
        data = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
        result = MetricsCalculator.percentile(data, 50)
        assert result == pytest.approx(55.0)

    def test_percentile_p95(self):
        data = list(range(1, 101))  # 1..100
        result = MetricsCalculator.percentile(data, 95)
        assert result == pytest.approx(95.05, rel=0.01)

    def test_percentile_p99(self):
        data = list(range(1, 101))
        result = MetricsCalculator.percentile(data, 99)
        assert result == pytest.approx(99.01, rel=0.01)

    def test_percentile_empty(self):
        assert MetricsCalculator.percentile([], 50) == 0.0

    def test_percentile_single_element(self):
        assert MetricsCalculator.percentile([42], 99) == 42.0

    def test_percentile_unsorted_input(self):
        """White-box: verify internal sort works."""
        data = [100, 1, 50, 25, 75]
        result = MetricsCalculator.percentile(data, 50)
        assert result == 50.0  # Median of sorted [1,25,50,75,100]


class TestMean:
    def test_mean_basic(self):
        assert MetricsCalculator.mean([10, 20, 30]) == pytest.approx(20.0)

    def test_mean_empty(self):
        assert MetricsCalculator.mean([]) == 0.0


class TestStdDev:
    def test_std_dev_zero_variance(self):
        """Constant array → std_dev = 0."""
        assert MetricsCalculator.std_dev([5, 5, 5, 5]) == 0.0

    def test_std_dev_known_value(self):
        data = [2, 4, 4, 4, 5, 5, 7, 9]
        result = MetricsCalculator.std_dev(data)
        assert result == pytest.approx(2.0, abs=0.1)

    def test_std_dev_single(self):
        assert MetricsCalculator.std_dev([10]) == 0.0


class TestDegradation:
    def test_degradation_flat(self):
        """Uniform latencies → ratio ≈ 1.0."""
        data = [100] * 100
        ratio = MetricsCalculator.degradation_ratio(data)
        assert ratio == pytest.approx(1.0)

    def test_degradation_exponential(self):
        """Exponentially growing → ratio >> 1."""
        data = [i ** 2 for i in range(1, 101)]
        ratio = MetricsCalculator.degradation_ratio(data)
        assert ratio > 5.0

    def test_degradation_empty(self):
        assert MetricsCalculator.degradation_ratio([]) == 1.0


class TestCurveMaxSpike:
    def test_flat_curve(self):
        data = [10, 10, 10, 10, 10]
        assert MetricsCalculator.curve_max_spike(data) == 0.0

    def test_single_spike(self):
        data = [10, 10, 10, 100, 10, 10, 10]
        spike = MetricsCalculator.curve_max_spike(data)
        assert spike > 5.0  # 90 jump / 10 median = 9.0

    def test_too_short(self):
        assert MetricsCalculator.curve_max_spike([1]) == 0.0
