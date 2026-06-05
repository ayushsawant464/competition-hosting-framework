"""Virtual call-stack frame tracker for the hardware emulation layer.

Replaces real Python call-stack tracking with a deterministic virtual
call stack.  Every function entry/exit in user code is intercepted by the
AST transformer, which calls ``push_frame`` / ``pop_frame`` on this
tracker via ``__emu__``.

Memory accounting is modelled after CPython internals:

* **Frame base cost** – A CPython ``PyFrameObject`` occupies roughly
  400 bytes (header + evaluation stack + bookkeeping pointers).
* **Per-local cost** – Each local variable slot is an 8-byte pointer on
  a 64-bit platform.

Because execution is single-threaded inside the sandbox, no locking is
required.
"""

from __future__ import annotations


class FrameTracker:
    """Tracks virtual call stack frames for recursion detection and memory accounting."""

    FRAME_BASE_BYTES: int = 400   # CPython PyFrameObject ≈ 400 bytes
    BYTES_PER_LOCAL: int = 8      # Each local variable is a pointer (8 bytes on 64-bit)

    def __init__(self, max_depth: int = 1000) -> None:
        self.max_depth: int = max_depth
        self.stack: list[tuple[str, int]] = []       # (func_name, frame_bytes)
        self.peak_depth: int = 0                     # Maximum depth reached
        self.total_calls: int = 0                    # Total function calls made
        self.call_counts: dict[str, int] = {}        # func_name -> count
        self.frame_memory: int = 0                   # Current bytes used by stack frames
        self.peak_frame_memory: int = 0              # Peak frame memory

    # ------------------------------------------------------------------
    # Stack operations
    # ------------------------------------------------------------------

    def push_frame(self, func_name: str, n_locals: int = 0) -> None:
        """Called on function entry.  Tracks depth and memory.

        Parameters
        ----------
        func_name:
            The qualified name of the function being entered.
        n_locals:
            Number of local variables declared in the function body.

        Raises
        ------
        RecursionError
            If the virtual stack depth exceeds *max_depth*.
        """
        frame_bytes: int = self.FRAME_BASE_BYTES + self.BYTES_PER_LOCAL * n_locals
        self.stack.append((func_name, frame_bytes))
        self.frame_memory += frame_bytes

        self.total_calls += 1
        self.peak_depth = max(self.peak_depth, len(self.stack))
        self.peak_frame_memory = max(self.peak_frame_memory, self.frame_memory)
        self.call_counts[func_name] = self.call_counts.get(func_name, 0) + 1

        if len(self.stack) > self.max_depth:
            raise RecursionError(
                f"Virtual stack overflow: depth {len(self.stack)} "
                f"exceeds limit {self.max_depth} "
                f"(last function: '{func_name}')"
            )

    def pop_frame(self) -> None:
        """Called on function return.  Decrements depth and frees frame memory."""
        if self.stack:
            _, frame_bytes = self.stack.pop()
            self.frame_memory -= frame_bytes

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def current_depth(self) -> int:
        """Return the current call-stack depth."""
        return len(self.stack)

    # ------------------------------------------------------------------
    # Profiling / introspection
    # ------------------------------------------------------------------

    def get_profile(self) -> dict[str, object]:
        """Return a summary profile of call stack usage.

        The returned dictionary contains:

        * ``peak_depth`` – deepest stack depth observed.
        * ``total_calls`` – total number of function entries.
        * ``peak_frame_memory_bytes`` – high-water mark for frame memory.
        * ``peak_frame_memory_mb`` – same value expressed in mebibytes.
        * ``unique_functions`` – number of distinct functions called.
        * ``call_counts`` – mapping of function name → invocation count.
        """
        return {
            "peak_depth": self.peak_depth,
            "total_calls": self.total_calls,
            "peak_frame_memory_bytes": self.peak_frame_memory,
            "peak_frame_memory_mb": self.peak_frame_memory / (1024 * 1024),
            "unique_functions": len(self.call_counts),
            "call_counts": dict(self.call_counts),  # Defensive copy
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Reset all state so the tracker can be reused between runs."""
        self.stack.clear()
        self.peak_depth = 0
        self.total_calls = 0
        self.call_counts.clear()
        self.frame_memory = 0
        self.peak_frame_memory = 0
