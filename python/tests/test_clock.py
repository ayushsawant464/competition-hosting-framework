"""Layer 1 Unit Tests: VirtualClock (black-box + white-box)"""
import pytest
from emu.clock import VirtualClock


class TestVirtualClock:

    def test_initial_state(self):
        clock = VirtualClock()
        assert clock.current_ns == 0
        assert clock.stamp() == 0

    def test_cycle_to_ns_conversion(self):
        """3000 cycles at 3.0 GHz → 1000 ns."""
        clock = VirtualClock()
        clock.advance_cycles(3000)
        assert clock.stamp() == 1000

    def test_single_cycle(self):
        """1 cycle at 3.0 GHz → 0 ns (int truncation)."""
        clock = VirtualClock()
        clock.advance_cycles(1)
        assert clock.stamp() == 0  # int(1/3.0) = 0

    def test_network_delay_additive(self):
        """Two delays stack correctly."""
        clock = VirtualClock()
        clock.add_network_delay(200)
        clock.add_network_delay(100)
        assert clock.stamp() == 300

    def test_mixed_cycles_and_delay(self):
        """Cycles + network delay combine."""
        clock = VirtualClock()
        clock.advance_cycles(3000)  # +1000 ns
        clock.add_network_delay(500)  # +500 ns
        assert clock.stamp() == 1500

    def test_stamp_monotonic(self):
        """White-box: stamps never decrease."""
        clock = VirtualClock()
        stamps = []
        for i in range(10):
            clock.advance_cycles(100)
            stamps.append(clock.stamp())
        for i in range(1, len(stamps)):
            assert stamps[i] >= stamps[i - 1]
