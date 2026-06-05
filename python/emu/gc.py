"""
Virtual Garbage Collection Module.

Simulates the performance cost of Python's mark-and-sweep garbage collector.
Users can interact with this module inside the sandbox via `import gc`.
"""

import types
from typing import Any

class VirtualGC:
    """Manages virtual garbage collection triggers and cycle costs."""
    
    def __init__(self, emu_state: Any) -> None:
        self.emu_state = emu_state
        self.allocation_count = 0
        self.threshold = 700  # CPython default generation 0 threshold
        self.enabled = True
        
    def track_allocation(self) -> None:
        """Called by the memory tracker whenever a new object is allocated."""
        if not self.enabled:
            return
        self.allocation_count += 1
        if self.allocation_count >= self.threshold:
            self.collect()

    def collect(self, generation: int = 2) -> int:
        """
        Simulate a garbage collection pause.
        
        The cost is proportional to the size of the active memory graph,
        as the GC must traverse active objects to find unreferenced ones.
        
        Returns:
            int: The estimated number of objects collected (simulated).
        """
        if not self.emu_state:
            return 0
            
        # Estimate active objects based on tracked allocations
        active_objects = len(self.emu_state.memory.allocations)
        
        # Cycle cost: Base overhead + O(N) traversal cost
        # CPython GC traversal is relatively fast but caches miss heavily.
        traversal_cost = active_objects * 12 
        gc_cycles = 1500 + traversal_cost
        
        self.emu_state.increment(gc_cycles)
        
        # Reset counter
        self.allocation_count = 0
        
        # We don't actually free memory here because our MemoryTracker 
        # frees memory deterministically when variables go out of scope.
        # We just return a mock collected count for realism.
        return active_objects // 10

    def disable(self) -> None:
        """Disable automatic garbage collection."""
        self.enabled = False

    def enable(self) -> None:
        """Enable automatic garbage collection."""
        self.enabled = True
        
    def get_threshold(self) -> tuple:
        """Return the current collection thresholds."""
        return (self.threshold, 10, 10)
        
    def set_threshold(self, threshold0: int, threshold1: int = 10, threshold2: int = 10) -> None:
        """Set the garbage collection thresholds."""
        self.threshold = threshold0

def create_gc_module(emu_state: Any) -> tuple[types.ModuleType, VirtualGC]:
    """Create a mock `gc` module for the sandbox."""
    gc_mod = types.ModuleType('gc')
    gc_mod.__doc__ = "Virtual garbage collection interface."
    
    vgc = VirtualGC(emu_state)
    
    # Bind module methods to the VirtualGC instance
    gc_mod.collect = vgc.collect
    gc_mod.disable = vgc.disable
    gc_mod.enable = vgc.enable
    gc_mod.get_threshold = vgc.get_threshold
    gc_mod.set_threshold = vgc.set_threshold
    gc_mod.isenabled = lambda: vgc.enabled
    
    return gc_mod, vgc
