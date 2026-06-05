"""
Stateless metrics utility module for computing statistical distributions.
Zero dependencies on the sandbox — takes arrays of numbers, returns stats.
"""
import math


class MetricsCalculator:

    @staticmethod
    def percentile(data: list, p: float) -> float:
        """
        Compute the p-th percentile using linear interpolation.
        p is in [0, 100].
        """
        if not data:
            return 0.0
        sorted_data = sorted(data)
        n = len(sorted_data)
        if n == 1:
            return float(sorted_data[0])
        rank = (p / 100.0) * (n - 1)
        low = int(math.floor(rank))
        high = int(math.ceil(rank))
        if low == high:
            return float(sorted_data[low])
        weight = rank - low
        return sorted_data[low] * (1 - weight) + sorted_data[high] * weight

    @staticmethod
    def mean(data: list) -> float:
        if not data:
            return 0.0
        return sum(data) / len(data)

    @staticmethod
    def std_dev(data: list) -> float:
        """Standard deviation of the dataset."""
        if len(data) < 2:
            return 0.0
        avg = sum(data) / len(data)
        variance = sum((x - avg) ** 2 for x in data) / len(data)
        return math.sqrt(variance)

    @staticmethod
    def degradation_ratio(data: list, bucket_pct: float = 0.10) -> float:
        """
        Compare avg of first bucket_pct% vs last bucket_pct%.
        Returns ratio >= 1.0 if performance degrades.
        """
        if not data or len(data) < 2:
            return 1.0
        bucket_size = max(1, int(len(data) * bucket_pct))
        first = data[:bucket_size]
        last = data[-bucket_size:]
        avg_first = sum(first) / len(first)
        avg_last = sum(last) / len(last)
        if avg_first == 0:
            return 1.0
        return avg_last / avg_first

    @staticmethod
    def curve_max_spike(data: list) -> float:
        """
        Max single-sample jump as a ratio of the median.
        Returns 0.0 for flat data, high values for spiky data.
        """
        if len(data) < 3:
            return 0.0
        median = sorted(data)[len(data) // 2]
        if median == 0:
            return 0.0
        max_jump = 0.0
        for i in range(1, len(data)):
            jump = abs(data[i] - data[i - 1])
            if jump > max_jump:
                max_jump = jump
        return max_jump / median
