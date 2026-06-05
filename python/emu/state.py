"""
State management for the emulation layer.

Follows the Single Responsibility Principle by isolating the core
simulation state (clock, memory, cycles) from the execution sandbox.
"""

import threading
from typing import Any

from emu.virtual_memory import MemoryTracker
from emu.clock import VirtualClock
from emu.frame_tracker import FrameTracker
from emu.method_costs import estimate_method_cost, is_data_science_method
from emu.gc import VirtualGC


class EmulationState:
    """Holds the global state for the emulation layer."""
    def __init__(self, memory_limit_mb: int = 512):
        self.virtual_cycles = 0
        self.memory = MemoryTracker(memory_limit_mb)
        self.clock = VirtualClock()
        self.frame_tracker = FrameTracker()
        self.virtual_gc = VirtualGC(self)
        self._lock = threading.Lock()
        self.preloaded_conn = None  # Set by BenchmarkEnvironment

    def increment(self, amount: int) -> None:
        """Advance the virtual clock and charge CPU cycles."""
        with self._lock:
            self.virtual_cycles += amount
            self.clock.advance_cycles(amount)
            # Sample memory every 10,000 cycles
            if self.virtual_cycles % 10000 < amount:
                self.memory.check_limit()

    def track_mem(self, name: str, obj: Any) -> None:
        """Hook to track memory allocations of named variables."""
        old_size = self.memory.allocations.get(name, 0)
        new_size = self.memory.track_object(name, obj)
        
        # Trigger Virtual GC tracking on new allocation
        if new_size > 0 and old_size == 0:
            self.virtual_gc.track_allocation()
            
        if new_size > old_size:
            # Charge base cost (50 cycles) only for new named allocations
            base = 50 if old_size == 0 else 0
            alloc_cycles = base + (new_size - old_size) // 16
            self.increment(alloc_cycles)

    def track_anonymous(self, size: int) -> None:
        """Hook to track temporary heap allocations."""
        self.memory.track_anonymous(size)
        if size > 0:
            # Charge cycles for anonymous heap allocation 
            # (30 cycles base + 1 cycle per 32 bytes)
            self.increment(30 + size // 32)
            # Trigger Virtual GC tracking on anonymous allocation
            self.virtual_gc.track_allocation()

    def free(self, name: str) -> None:
        """Free a named allocation."""
        self.memory.free(name)

    def clear_anonymous(self) -> None:
        """Clear temporary allocations at statement boundaries."""
        self.memory.clear_anonymous()

    def push_frame(self, func_name: str, n_locals: int = 0) -> None:
        """Push a new frame to the virtual call stack."""
        old_mem = self.frame_tracker.frame_memory
        self.frame_tracker.push_frame(func_name, n_locals)
        delta = self.frame_tracker.frame_memory - old_mem
        if delta > 0:
            self.memory.track_anonymous(delta)

    def pop_frame(self) -> None:
        """Pop the current frame from the virtual call stack."""
        old_mem = self.frame_tracker.frame_memory
        self.frame_tracker.pop_frame()
        delta = old_mem - self.frame_tracker.frame_memory
        if delta > 0:
            self.memory.current_bytes = max(
                self.memory._STARTUP_OVERHEAD_BYTES, 
                self.memory.current_bytes - delta
            )

    def call_method(self, obj: Any, method_name: str, *args: Any, **kwargs: Any) -> Any:
        """
        Intercept method calls to charge cycles based on data size and algorithmic complexity.
        Delegates complexity math to method_costs module.
        """
        # 1. Compute cycles (fail-safe) using method cost estimator
        cycles = estimate_method_cost(obj, method_name, args, kwargs)

        # Scale down C built-in cycles to reflect native execution speed relative to Python interpreter loop
        cycles = max(1, cycles // 150)
        self.increment(cycles)

        # 2. Call the real method
        real_method = getattr(obj, method_name)
        result = real_method(*args, **kwargs)

        # 3. Track output allocation memory anonymously
        try:
            from emu.virtual_memory import estimate_object_size
            size = estimate_object_size(result)
            self.track_anonymous(size)
            
            if is_data_science_method(obj) and method_name in ('read_csv', 'read_parquet', 'DataFrame', 'Series'):
                # Charge 1 cycle per 16 bytes loaded to mock file parsing & data loading
                parse_cycles = max(1, (size // 16) // 150)
                self.increment(parse_cycles)
        except Exception:
            pass

        return result
