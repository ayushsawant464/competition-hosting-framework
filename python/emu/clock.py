"""
Discrete Event Simulation virtual clock.
Converts CPU cycles to nanoseconds using a declared hardware spec.
"""


class VirtualClock:
    CPU_GHZ = 3.0  # Virtual 3.0 GHz CPU: 1 cycle ≈ 0.333 ns

    def __init__(self):
        self.current_ns = 0

    def advance_cycles(self, cycles: int):
        """Convert cycles to nanoseconds and advance the clock."""
        self.current_ns += int(cycles / self.CPU_GHZ)

    def add_network_delay(self, delay_ns: int):
        """Add a fixed hardware delay (e.g. PCI-e bus, NIC DMA)."""
        self.current_ns += delay_ns

    def stamp(self) -> int:
        """Return the current virtual time in nanoseconds."""
        return self.current_ns
