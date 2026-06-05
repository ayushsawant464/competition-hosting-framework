"""Deterministic structural memory tracker for the IICPC emulation layer.

Replaces tracemalloc-based memory tracking (which is subject to GC noise and
CPython allocator non-determinism) with a structural estimator that computes
object sizes from their type and length. This makes memory scores 100%
deterministic across runs and machines.
"""

from __future__ import annotations
import types
from typing import Any, Dict, List, Set, Optional

class MemoryTracker:
    """Deterministic memory tracker using structural size estimation.

    Instead of tracemalloc (which measures real CPython allocations and is
    subject to GC noise), this tracks memory by estimating object sizes
    from their type and length. This makes memory scores 100% deterministic.

    CPython 64-bit object sizes (empirically measured)::

        int (small):    28 bytes
        float:          24 bytes
        bool:           28 bytes  (inherits int)
        str:            49 + len(s) bytes  (compact ASCII)
        bytes:          33 + len(b) bytes
        list:           56 + 8 * len(l) bytes  (header + pointer array)
        tuple:          40 + 8 * len(t) bytes
        dict:           64 + 72 * len(d) bytes  (compact dict, Python 3.6+)
        set:            200 + 8 * len(s) bytes
        NoneType:       16 bytes
        generic object: 56 bytes
    """

    _STARTUP_OVERHEAD_BYTES: int = 32768  # 32 KB baseline for CPython VM overhead
    _container_cache: dict[int, tuple[int, int]] = {}

    def __init__(self, limit_mb: int = 512) -> None:
        self.limit_bytes: int = limit_mb * 1024 * 1024
        self.current_bytes: int = self._STARTUP_OVERHEAD_BYTES
        self.peak_bytes: int = self._STARTUP_OVERHEAD_BYTES
        self.anonymous_bytes: int = 0
        self.timeline: list[float] = []        # Memory samples (MB) at each sample point
        self.allocations: dict[str, int] = {}  # name → estimated_bytes
        self._sample_interval: int = 10000     # Sample every N virtual cycles
        self._running: bool = False

    def start(self) -> None:
        """Begin tracking."""
        self._running = True
        MemoryTracker._container_cache.clear()
        self.current_bytes = self._STARTUP_OVERHEAD_BYTES
        self.peak_bytes = self._STARTUP_OVERHEAD_BYTES
        self.anonymous_bytes = 0
        self.timeline = []
        self.allocations = {}

    def stop(self) -> None:
        """Stop tracking."""
        self._running = False

    def track_object(self, name: str, obj: object) -> int:
        """Track or update a named allocation.

        Called by AST-injected hooks after assignments::

            __emu__.track_mem('my_list', my_list)

        Returns the estimated size in bytes.
        """
        if not self._running:
            return 0

        # Ignore modules, functions, classes, methods to avoid tracking code/metadata
        if isinstance(obj, (types.ModuleType, types.FunctionType, types.BuiltinFunctionType, types.MethodType)) or isinstance(obj, type) or callable(obj):
            return 0

        new_size = self._estimate_size(obj)
        old_size = self.allocations.get(name, 0)
        delta = new_size - old_size

        self.allocations[name] = new_size
        self.current_bytes += delta

        if self.current_bytes > self.peak_bytes:
            self.peak_bytes = self.current_bytes

        if self.current_bytes > self.limit_bytes:
            raise MemoryError(
                f"Virtual memory limit exceeded! "
                f"Used {self.current_bytes / 1024 / 1024:.2f}MB, "
                f"Limit {self.limit_bytes / 1024 / 1024:.2f}MB"
            )

        return new_size

    def track_anonymous(self, estimated_bytes: int) -> None:
        """Track an anonymous allocation (e.g., from a builtin return value)."""
        if not self._running:
            return
        self.anonymous_bytes += estimated_bytes
        self.current_bytes += estimated_bytes
        if self.current_bytes > self.peak_bytes:
            self.peak_bytes = self.current_bytes

    def clear_anonymous(self) -> None:
        """Reset temporary anonymous memory allocation tracking (garbage collected at statement end)."""
        if not self._running:
            return
        self.current_bytes = max(self._STARTUP_OVERHEAD_BYTES, self.current_bytes - self.anonymous_bytes)
        self.anonymous_bytes = 0

    def free(self, name: str) -> None:
        """Free a named allocation (e.g., variable goes out of scope)."""
        if name in self.allocations:
            self.current_bytes -= self.allocations.pop(name)
            self.current_bytes = max(self._STARTUP_OVERHEAD_BYTES, self.current_bytes)

    def sample(self) -> None:
        """Record a timeline sample. Called periodically by ``EmulationState.increment()``."""
        if self._running:
            self.timeline.append(self.current_bytes / 1024 / 1024)  # Store as MB

    def check_limit(self) -> None:
        """Check if memory limit is exceeded. Also records a sample."""
        # Dynamic scanning of stack frames to detect allocations (fail-safe and automatic)
        import sys
        if self._running:
            try:
                frame = sys._getframe(1)
                active_names = set()
                while frame:
                    filename = frame.f_code.co_filename
                    if '<benchmark>' in filename or 'test_' in filename or '<module>' in filename:
                        for name, val in frame.f_locals.items():
                            if name.startswith('__') and name.endswith('__'):
                                continue
                            qualified_name = f"frame_{id(frame)}_{name}"
                            self.track_object(qualified_name, val)
                            active_names.add(qualified_name)
                    frame = frame.f_back

                # Clean up allocations that are no longer in scope
                for name in list(self.allocations.keys()):
                    if name.startswith('frame_') and name not in active_names:
                        self.free(name)
            except Exception:
                pass

        self.sample()
        if self._running and self.current_bytes > self.limit_bytes:
            raise MemoryError(
                f"Virtual memory limit exceeded! "
                f"Used {self.current_bytes / 1024 / 1024:.2f}MB, "
                f"Limit {self.limit_bytes / 1024 / 1024:.2f}MB"
            )

    def get_peak_mb(self) -> float:
        """Return peak memory usage in megabytes."""
        return self.peak_bytes / 1024 / 1024

    def get_memory_profile(self) -> dict:
        """Compute memory distribution from timeline."""
        # Late import to avoid circular dependency
        from emu.metrics import MetricsCalculator

        if not self.timeline:
            return {
                'p50_mb': 0.0,
                'p95_mb': 0.0,
                'p99_mb': 0.0,
                'volatility': 0.0,
                'max_spike': 0.0,
            }

        mc = MetricsCalculator
        return {
            'p50_mb': mc.percentile(self.timeline, 50),
            'p95_mb': mc.percentile(self.timeline, 95),
            'p99_mb': mc.percentile(self.timeline, 99),
            'volatility': mc.std_dev(self.timeline),
            'max_spike': mc.curve_max_spike(self.timeline),
        }

    @staticmethod
    def _estimate_size(obj: object, visited: Set[int] | None = None) -> int:
        """Deterministic recursive size estimation based on type and structure."""
        if visited is None:
            visited = set()

        obj_id = id(obj)
        if obj_id in visited:
            return 0
        visited.add(obj_id)

        if obj is None:
            return 16

        # --- bool (must precede int) ---
        # --- list, tuple, dict, set container caching to prevent O(N^2) blowup ---
        if isinstance(obj, (list, tuple, dict, set)):
            try:
                obj_len = len(obj)
                cached = MemoryTracker._container_cache.get(id(obj))
                if cached is not None:
                    last_len, last_size = cached
                    if abs(obj_len - last_len) <= max(5, last_len // 20):
                        return last_size
            except Exception:
                pass

        # --- bool (must precede int) ---
        if isinstance(obj, bool):
            return 28

        # --- int ---
        if isinstance(obj, int):
            # Small ints are interned by CPython; don't charge them.
            if -5 <= obj <= 256:
                return 0
            n = obj.bit_length()
            return 28 + max(0, (n - 30)) // 30 * 4

        # --- float ---
        if isinstance(obj, float):
            return 24

        # --- str ---
        if isinstance(obj, str):
            return 49 + len(obj)

        # --- bytes ---
        if isinstance(obj, bytes):
            return 33 + len(obj)

        # --- bytearray ---
        if isinstance(obj, bytearray):
            return 56 + len(obj)

        # --- list ---
        if isinstance(obj, list):
            res = 56 + 8 * len(obj) + sum(MemoryTracker._estimate_size(x, visited) for x in obj)
            MemoryTracker._container_cache[id(obj)] = (len(obj), res)
            return res

        # --- tuple ---
        if isinstance(obj, tuple):
            res = 40 + 8 * len(obj) + sum(MemoryTracker._estimate_size(x, visited) for x in obj)
            MemoryTracker._container_cache[id(obj)] = (len(obj), res)
            return res

        # --- dict ---
        if isinstance(obj, dict):
            total = 64 + 72 * len(obj)
            for k, v in obj.items():
                total += MemoryTracker._estimate_size(k, visited) + MemoryTracker._estimate_size(v, visited)
            MemoryTracker._container_cache[id(obj)] = (len(obj), total)
            return total

        # --- set ---
        if isinstance(obj, set):
            res = 200 + 8 * len(obj) + sum(MemoryTracker._estimate_size(x, visited) for x in obj)
            MemoryTracker._container_cache[id(obj)] = (len(obj), res)
            return res

        # --- frozenset ---
        if isinstance(obj, frozenset):
            res = 200 + 8 * len(obj) + sum(MemoryTracker._estimate_size(x, visited) for x in obj)
            return res

        # --- pandas DataFrame / Series ---
        if hasattr(obj, 'memory_usage') and callable(getattr(obj, 'memory_usage')):
            try:
                val = obj.memory_usage(deep=True)
                if hasattr(val, 'sum') and callable(getattr(val, 'sum')):
                    return int(val.sum())
                return int(val)
            except Exception:
                pass

        # --- polars DataFrame / Series ---
        if hasattr(obj, 'estimated_size') and callable(getattr(obj, 'estimated_size')):
            try:
                return int(obj.estimated_size())
            except Exception:
                pass

        # --- generic object ---
        base = 56
        if hasattr(obj, '__dict__'):
            base += 64 + 72 * len(obj.__dict__)
        return base

# Aliases for compatibility
VirtualRAM = MemoryTracker

def estimate_object_size(obj: object) -> int:
    """Estimate the in-memory size of *obj* in bytes."""
    return MemoryTracker._estimate_size(obj)
