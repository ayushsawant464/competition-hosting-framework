"""Layer 2 Component Tests: MemoryTracker with timeline and profile."""
import pytest
from emu.virtual_memory import MemoryTracker


class TestMemoryTimeline:
    """Black-box: verify timeline grows and profile computes correctly."""

    def test_timeline_grows(self):
        """After N check_limit() calls, timeline has N entries."""
        tracker = MemoryTracker(limit_mb=512)
        tracker.start()
        for _ in range(5):
            tracker.check_limit()
        tracker.stop()
        assert len(tracker.timeline) == 5

    def test_timeline_records_positive_values(self):
        """All timeline entries should be >= 0."""
        tracker = MemoryTracker(limit_mb=512)
        tracker.start()
        # Allocate something so tracemalloc has data
        _big = [0] * 1000
        for _ in range(3):
            tracker.check_limit()
        tracker.stop()
        for val in tracker.timeline:
            assert val >= 0

    def test_profile_returns_all_keys(self):
        """get_memory_profile() returns p50, p95, p99, volatility, max_spike."""
        tracker = MemoryTracker(limit_mb=512)
        tracker.start()
        for _ in range(10):
            tracker.check_limit()
        tracker.stop()
        profile = tracker.get_memory_profile()
        assert 'p50_mb' in profile
        assert 'p95_mb' in profile
        assert 'p99_mb' in profile
        assert 'volatility' in profile
        assert 'max_spike' in profile

    def test_empty_profile(self):
        """No samples → all zeros."""
        tracker = MemoryTracker(limit_mb=512)
        profile = tracker.get_memory_profile()
        assert profile['p50_mb'] == 0.0


class TestMemoryLimit:
    """White-box: verify MemoryError is still raised."""

    def test_memory_limit_enforced(self):
        tracker = MemoryTracker(limit_mb=1)  # Tiny 1MB limit
        tracker.start()
        with pytest.raises(MemoryError):
            # Allocate more than 1MB
            _big = bytearray(2 * 1024 * 1024)
            tracker.check_limit()
        # Cleanup even if no error
        try:
            tracker.stop()
        except Exception:
            pass

    def test_peak_tracked(self):
        tracker = MemoryTracker(limit_mb=512)
        tracker.start()
        _data = [0] * 10000
        tracker.check_limit()
        tracker.stop()
        assert tracker.get_peak_mb() > 0
