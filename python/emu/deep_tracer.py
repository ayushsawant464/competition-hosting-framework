"""
deep_tracer.py — Comprehensive deep execution tracer for real Python execution.

Captures everything that happens during Python code execution at the finest
possible granularity: line-level traces, bytecode instruction counting, memory
tracking, I/O operation tracking, function call profiling, and hardware-level
metrics. Designed to produce ground-truth data from real hardware execution
that can be compared against a virtual emulation framework.

Self-contained: no dependencies on the emu package.
"""

from __future__ import annotations

import dis
import gc
import sys
import time
import tracemalloc
import types
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Opcode → category mapping
# ---------------------------------------------------------------------------

_OPCODE_CATEGORIES: Dict[str, str] = {}

_NAME_LOOKUP_OPS = frozenset({
    "LOAD_FAST", "LOAD_FAST_CHECK", "LOAD_FAST_AND_CLEAR",
    "LOAD_GLOBAL", "LOAD_NAME", "LOAD_DEREF", "LOAD_CLOSURE",
    "STORE_FAST", "STORE_GLOBAL", "STORE_NAME", "STORE_DEREF",
    "DELETE_FAST", "DELETE_GLOBAL", "DELETE_NAME", "DELETE_DEREF",
    "LOAD_FAST_LOAD_FAST", "STORE_FAST_STORE_FAST",
    "LOAD_FROM_DICT_OR_DEREF", "LOAD_FROM_DICT_OR_GLOBALS",
    "LOAD_LOCALS", "LOAD_SUPER_ATTR",
    # 3.10 and earlier aliases
    "LOAD_FAST__LOAD_FAST", "STORE_FAST__STORE_FAST",
})

_ARITHMETIC_OPS = frozenset({
    "BINARY_OP", "COMPARE_OP", "UNARY_NEGATIVE", "UNARY_POSITIVE",
    "UNARY_NOT", "UNARY_INVERT", "IS_OP", "CONTAINS_OP",
    # Pre-3.12 specific binary/inplace ops
    "BINARY_ADD", "BINARY_SUBTRACT", "BINARY_MULTIPLY",
    "BINARY_FLOOR_DIVIDE", "BINARY_TRUE_DIVIDE", "BINARY_MODULO",
    "BINARY_POWER", "BINARY_LSHIFT", "BINARY_RSHIFT",
    "BINARY_AND", "BINARY_OR", "BINARY_XOR", "BINARY_MATRIX_MULTIPLY",
    "INPLACE_ADD", "INPLACE_SUBTRACT", "INPLACE_MULTIPLY",
    "INPLACE_FLOOR_DIVIDE", "INPLACE_TRUE_DIVIDE", "INPLACE_MODULO",
    "INPLACE_POWER", "INPLACE_LSHIFT", "INPLACE_RSHIFT",
    "INPLACE_AND", "INPLACE_OR", "INPLACE_XOR", "INPLACE_MATRIX_MULTIPLY",
})

_CONTROL_FLOW_OPS = frozenset({
    "FOR_ITER", "GET_ITER", "GET_YIELD_FROM_ITER",
    "JUMP_FORWARD", "JUMP_BACKWARD", "JUMP_BACKWARD_NO_INTERRUPT",
    "JUMP_ABSOLUTE",
    "POP_JUMP_IF_TRUE", "POP_JUMP_IF_FALSE",
    "POP_JUMP_IF_NONE", "POP_JUMP_IF_NOT_NONE",
    "POP_JUMP_FORWARD_IF_TRUE", "POP_JUMP_FORWARD_IF_FALSE",
    "POP_JUMP_FORWARD_IF_NONE", "POP_JUMP_FORWARD_IF_NOT_NONE",
    "POP_JUMP_BACKWARD_IF_TRUE", "POP_JUMP_BACKWARD_IF_FALSE",
    "POP_JUMP_BACKWARD_IF_NONE", "POP_JUMP_BACKWARD_IF_NOT_NONE",
    "JUMP_IF_TRUE_OR_POP", "JUMP_IF_FALSE_OR_POP",
    "SETUP_LOOP", "SETUP_EXCEPT", "SETUP_FINALLY", "SETUP_WITH",
    "SETUP_ASYNC_WITH",
    "END_FOR", "END_SEND",
    "RETURN_VALUE", "RETURN_CONST", "RETURN_GENERATOR",
    "YIELD_VALUE", "SEND",
    "RAISE_VARARGS", "RERAISE",
    "BREAK_LOOP", "CONTINUE_LOOP",
})

_FUNCTION_CALL_OPS = frozenset({
    "CALL", "CALL_FUNCTION", "CALL_FUNCTION_EX", "CALL_FUNCTION_KW",
    "CALL_KW", "CALL_METHOD", "CALL_INTRINSIC_1", "CALL_INTRINSIC_2",
    "PUSH_NULL", "KW_NAMES",
    "MAKE_FUNCTION", "MAKE_CELL",
    "LOAD_METHOD",  # Pre-3.12
})

_DATA_ACCESS_OPS = frozenset({
    "LOAD_ATTR", "STORE_ATTR", "DELETE_ATTR",
    "BINARY_SUBSCR", "STORE_SUBSCR", "DELETE_SUBSCR",
    "BUILD_TUPLE", "BUILD_LIST", "BUILD_SET", "BUILD_MAP",
    "BUILD_CONST_KEY_MAP", "BUILD_STRING", "BUILD_SLICE",
    "LIST_APPEND", "SET_ADD", "MAP_ADD", "LIST_EXTEND",
    "SET_UPDATE", "DICT_UPDATE", "DICT_MERGE",
    "UNPACK_SEQUENCE", "UNPACK_EX",
    "LOAD_CONST",
})


def _build_opcode_category_map() -> Dict[str, str]:
    """Build and cache the opcode → category lookup table."""
    if _OPCODE_CATEGORIES:
        return _OPCODE_CATEGORIES
    for name in _NAME_LOOKUP_OPS:
        _OPCODE_CATEGORIES[name] = "name_lookups"
    for name in _ARITHMETIC_OPS:
        _OPCODE_CATEGORIES[name] = "arithmetic"
    for name in _CONTROL_FLOW_OPS:
        _OPCODE_CATEGORIES[name] = "control_flow"
    for name in _FUNCTION_CALL_OPS:
        _OPCODE_CATEGORIES[name] = "function_calls"
    for name in _DATA_ACCESS_OPS:
        _OPCODE_CATEGORIES[name] = "data_access"
    return _OPCODE_CATEGORIES


def _categorize_opcode(opname: str) -> str:
    """Return the category string for a given opcode name."""
    cats = _build_opcode_category_map()
    cat = cats.get(opname)
    if cat is not None:
        return cat
    # Heuristic fallback for unknown opcodes
    upper = opname.upper()
    if "LOAD" in upper or "STORE" in upper or "DELETE" in upper:
        return "name_lookups"
    if "JUMP" in upper or "LOOP" in upper or "ITER" in upper:
        return "control_flow"
    if "CALL" in upper:
        return "function_calls"
    if "BINARY" in upper or "UNARY" in upper or "INPLACE" in upper:
        return "arithmetic"
    return "other"


# ---------------------------------------------------------------------------
# Bytecode analysis
# ---------------------------------------------------------------------------

def count_bytecode_ops(code_obj: types.CodeType) -> Dict[str, Dict[str, int]]:
    """Recursively count bytecode operations in *code_obj* and all nested
    code objects found in ``co_consts``.

    Returns a dict mapping ``category_name`` → ``{opcode_name: count}``.
    A top-level ``"_totals"`` key maps categories to their aggregate counts.
    """
    category_ops: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))

    def _walk(co: types.CodeType) -> None:
        for instr in dis.get_instructions(co):
            cat = _categorize_opcode(instr.opname)
            category_ops[cat][instr.opname] += 1
        # Recurse into nested code objects (comprehensions, lambdas, inner
        # functions, class bodies, etc.)
        for const in co.co_consts:
            if isinstance(const, types.CodeType):
                _walk(const)

    _walk(code_obj)

    # Build totals
    totals: Dict[str, int] = {}
    for cat, ops in category_ops.items():
        totals[cat] = sum(ops.values())
    # Freeze nested defaultdicts into plain dicts for cleanliness
    result: Dict[str, Any] = {cat: dict(ops) for cat, ops in category_ops.items()}
    result["_totals"] = totals
    return result


# ---------------------------------------------------------------------------
# Trace event tuple layout (for speed, we store tuples not dicts)
# ---------------------------------------------------------------------------
# Index  Field
# 0      event_type   ("line" | "call" | "return" | "exception")
# 1      timestamp_ns (int)
# 2      filename     (str)
# 3      lineno       (int)
# 4      func_name    (str)
# 5      extra        (return_value_type str | exception info tuple | None)

_EVT_TYPE = 0
_EVT_TS = 1
_EVT_FILE = 2
_EVT_LINE = 3
_EVT_FUNC = 4
_EVT_EXTRA = 5


# ---------------------------------------------------------------------------
# I/O wrapper helpers
# ---------------------------------------------------------------------------

class _IOTracker:
    """Lightweight wrapper that monkey-patches socket methods to record I/O."""

    __slots__ = ("records", "cumulative_bytes", "_patched")

    def __init__(self) -> None:
        self.records: List[Tuple[int, str, int, int]] = []  # ts, op, nbytes, cum
        self.cumulative_bytes: int = 0
        self._patched: List[Tuple[Any, str, Any]] = []  # (obj, attr, original)

    # -- patching helpers --------------------------------------------------

    def _wrap_method(self, cls: type, method_name: str) -> None:
        original = getattr(cls, method_name, None)
        if original is None:
            return

        tracker = self  # closure reference

        def wrapper(self_sock, *args, **kwargs):  # noqa: N805 — intentional
            result = original(self_sock, *args, **kwargs)
            nbytes = 0
            if isinstance(result, (bytes, bytearray, memoryview)):
                nbytes = len(result)
            elif isinstance(result, int):
                nbytes = result
            elif isinstance(result, tuple) and result and isinstance(result[0], (bytes, bytearray)):
                nbytes = len(result[0])
            # For send/sendall the byte count is the *input* length
            if method_name in ("send", "sendall") and args:
                data = args[0]
                if isinstance(data, (bytes, bytearray, memoryview)):
                    nbytes = len(data)
            tracker.cumulative_bytes += nbytes
            tracker.records.append((
                time.perf_counter_ns(),
                method_name,
                nbytes,
                tracker.cumulative_bytes,
            ))
            return result

        self._patched.append((cls, method_name, original))
        setattr(cls, method_name, wrapper)

    def install(self) -> None:
        """Monkey-patch socket.socket to intercept I/O."""
        try:
            import socket as _socket_mod
            for mname in ("recv", "recv_into", "recvfrom", "recvmsg",
                           "send", "sendall", "sendto", "sendmsg"):
                self._wrap_method(_socket_mod.socket, mname)
        except Exception:
            pass  # socket not available — skip

    def uninstall(self) -> None:
        """Restore original socket methods."""
        for cls, attr, original in reversed(self._patched):
            try:
                setattr(cls, attr, original)
            except Exception:
                pass
        self._patched.clear()


# ---------------------------------------------------------------------------
# DeepTracer
# ---------------------------------------------------------------------------

class DeepTracer:
    """Records everything that happens during real Python execution.

    Usage::

        tracer = DeepTracer()
        summary = tracer.trace_execution(source_code, globals_dict)
    """

    def __init__(self) -> None:
        # Raw event buffer — stored as tuples for minimal overhead.
        self.events: List[Tuple] = []

        # Bytecode profile: func_qualname -> {category -> count}
        self.bytecode_profile: Dict[str, Dict[str, int]] = {}

        # Memory snapshots: list of (timestamp_ns, current_bytes, peak_bytes)
        self.memory_snapshots: List[Tuple[int, int, int]] = []

        # I/O operations: list of (timestamp_ns, op, byte_count, cumulative)
        self.io_operations: List[Tuple[int, str, int, int]] = []

        # Call profile: func_qualname -> {"calls": int, "total_ns": int,
        #   "children": set[str]}
        self.call_profile: Dict[str, Dict[str, Any]] = {}

        # GC stats captured before and after execution.
        self.gc_stats: Dict[str, Any] = {}

        # Per-line wall-clock durations: (filename, lineno) -> [duration_ns, ...]
        self.line_timings: Dict[Tuple[str, int], List[int]] = defaultdict(list)

        # ---- internal bookkeeping ----
        self._wall_start_ns: int = 0
        self._wall_end_ns: int = 0
        self._cpu_start_ns: int = 0
        self._cpu_end_ns: int = 0
        self._call_stack: List[Tuple[str, int]] = []  # (func_qualname, enter_ts)
        self._peak_depth: int = 0
        self._prev_line_ts: int = 0
        self._prev_line_key: Optional[Tuple[str, int]] = None
        self._io_tracker: Optional[_IOTracker] = None

    # ----- trace callback (hot path — keep lean) --------------------------

    def _trace_callback(self, frame: types.FrameType, event: str, arg: Any):
        """sys.settrace callback.  Called on every line / call / return /
        exception event for every frame.  Must return itself for the frame
        to keep being traced."""
        ts = time.perf_counter_ns()
        co = frame.f_code
        fname = co.co_filename
        lineno = frame.f_lineno
        funcname = co.co_qualname if hasattr(co, "co_qualname") else co.co_name

        if event == "line":
            # Record line-level timing for the *previous* line
            if self._prev_line_key is not None:
                dur = ts - self._prev_line_ts
                self.line_timings[self._prev_line_key].append(dur)
            self._prev_line_key = (fname, lineno)
            self._prev_line_ts = ts
            self.events.append((event, ts, fname, lineno, funcname, None))

        elif event == "call":
            self._call_stack.append((funcname, ts))
            depth = len(self._call_stack)
            if depth > self._peak_depth:
                self._peak_depth = depth
            # Record caller → callee edge
            if depth >= 2:
                caller = self._call_stack[-2][0]
                prof = self.call_profile.get(caller)
                if prof is not None:
                    prof["children"].add(funcname)
            # Ensure profile entry exists
            if funcname not in self.call_profile:
                self.call_profile[funcname] = {
                    "calls": 0, "total_ns": 0, "children": set()
                }
            self.call_profile[funcname]["calls"] += 1
            self.events.append((event, ts, fname, lineno, funcname, None))

        elif event == "return":
            ret_type = type(arg).__name__
            self.events.append((event, ts, fname, lineno, funcname, ret_type))
            if self._call_stack and self._call_stack[-1][0] == funcname:
                _, enter_ts = self._call_stack.pop()
                elapsed = ts - enter_ts
                self.call_profile[funcname]["total_ns"] += elapsed
            # Close previous line timing
            if self._prev_line_key is not None:
                dur = ts - self._prev_line_ts
                self.line_timings[self._prev_line_key].append(dur)
                self._prev_line_key = None

        elif event == "exception":
            exc_type, exc_value, _tb = arg
            extra = (exc_type.__name__, str(exc_value))
            self.events.append((event, ts, fname, lineno, funcname, extra))

        return self._trace_callback

    # ----- memory snapshot helper -----------------------------------------

    def _take_memory_snapshot(self) -> None:
        """Take a tracemalloc snapshot and append to memory_snapshots."""
        if not tracemalloc.is_tracing():
            return
        ts = time.perf_counter_ns()
        current, peak = tracemalloc.get_traced_memory()
        self.memory_snapshots.append((ts, current, peak))

    # ----- bytecode pre-analysis ------------------------------------------

    def _analyze_bytecode(self, code_obj: types.CodeType) -> None:
        """Pre-analyze bytecode of the compiled code object."""
        profile = count_bytecode_ops(code_obj)
        # Store under a synthetic name for the top-level module code.
        self.bytecode_profile["<module>"] = profile

        # Also walk nested code objects and store per-function profiles.
        def _walk(co: types.CodeType) -> None:
            name = co.co_qualname if hasattr(co, "co_qualname") else co.co_name
            if name != "<module>":
                self.bytecode_profile[name] = count_bytecode_ops(co)
            for const in co.co_consts:
                if isinstance(const, types.CodeType):
                    _walk(const)
        _walk(code_obj)

    # ----- public API -----------------------------------------------------

    def trace_execution(self, code_str: str, globals_ns: Optional[dict] = None) -> dict:
        """Execute *code_str* with full tracing enabled.

        Parameters
        ----------
        code_str : str
            Python source code to execute.
        globals_ns : dict, optional
            Global namespace dict for execution.  A fresh dict is used when
            *None*.

        Returns
        -------
        dict
            The comprehensive summary produced by :meth:`get_summary`.
        """
        if globals_ns is None:
            globals_ns = {"__builtins__": __builtins__}
        elif "__builtins__" not in globals_ns:
            globals_ns["__builtins__"] = __builtins__

        # 1. Compile -------------------------------------------------------
        code_obj = compile(code_str, "<traced>", "exec")

        # 2. Bytecode pre-analysis -----------------------------------------
        self._analyze_bytecode(code_obj)

        # 3. GC stats — before ---------------------------------------------
        gc.collect()
        self.gc_stats["before"] = [dict(s) for s in gc.get_stats()]

        # 4. Start tracemalloc ---------------------------------------------
        was_tracing = tracemalloc.is_tracing()
        if not was_tracing:
            tracemalloc.start(25)
        else:
            tracemalloc.reset_peak()
        self._take_memory_snapshot()

        # 5. Install I/O tracker -------------------------------------------
        self._io_tracker = _IOTracker()
        self._io_tracker.install()

        # 6. Install sys.settrace ------------------------------------------
        old_trace = sys.gettrace()
        sys.settrace(self._trace_callback)

        # 7. Record start timestamps ---------------------------------------
        self._wall_start_ns = time.perf_counter_ns()
        self._cpu_start_ns = time.process_time_ns()

        # 8. Execute -------------------------------------------------------
        exec_exception: Optional[BaseException] = None
        try:
            exec(code_obj, globals_ns)
        except BaseException as exc:
            exec_exception = exc
        finally:
            # 9. Record end timestamps -------------------------------------
            self._wall_end_ns = time.perf_counter_ns()
            self._cpu_end_ns = time.process_time_ns()

            # 10. Remove trace hook ----------------------------------------
            sys.settrace(old_trace)

            # 11. Final memory snapshot ------------------------------------
            self._take_memory_snapshot()

            # 12. GC stats — after -----------------------------------------
            gc.collect()
            self.gc_stats["after"] = [dict(s) for s in gc.get_stats()]

            # 13. Tear down I/O tracker ------------------------------------
            if self._io_tracker is not None:
                self.io_operations = list(self._io_tracker.records)
                self._io_tracker.uninstall()
                self._io_tracker = None

            # 14. Stop tracemalloc if we started it ------------------------
            if not was_tracing and tracemalloc.is_tracing():
                tracemalloc.stop()

            # Close any dangling previous-line timing
            if self._prev_line_key is not None:
                dur = self._wall_end_ns - self._prev_line_ts
                self.line_timings[self._prev_line_key].append(dur)
                self._prev_line_key = None

        summary = self.get_summary()

        # Attach execution exception info if one occurred
        if exec_exception is not None:
            summary["exec_exception"] = {
                "type": type(exec_exception).__name__,
                "message": str(exec_exception),
            }

        return summary

    # ----- summary builder ------------------------------------------------

    def get_summary(self) -> dict:
        """Return a comprehensive summary of all traced metrics."""
        total_wall_ns = self._wall_end_ns - self._wall_start_ns
        total_cpu_ns = self._cpu_end_ns - self._cpu_start_ns

        # Lines executed
        line_events = [e for e in self.events if e[_EVT_TYPE] == "line"]
        total_lines = len(line_events)

        # Bytecode breakdown — aggregate across all functions
        agg_bytecode: Dict[str, int] = defaultdict(int)
        total_bytecodes = 0
        for _fname, profile in self.bytecode_profile.items():
            totals = profile.get("_totals", {})
            for cat, cnt in totals.items():
                agg_bytecode[cat] += cnt
                total_bytecodes += cnt

        # Memory
        peak_mem = 0
        for _ts, _cur, peak in self.memory_snapshots:
            if peak > peak_mem:
                peak_mem = peak

        # I/O
        total_io_calls = len(self.io_operations)
        total_io_bytes = self.io_operations[-1][3] if self.io_operations else 0

        # Function profiling
        total_func_calls = sum(p["calls"] for p in self.call_profile.values())
        unique_funcs = len(self.call_profile)

        # GC
        gc_collections = 0
        gc_collected = 0
        before = self.gc_stats.get("before", [])
        after = self.gc_stats.get("after", [])
        for b, a in zip(before, after):
            gc_collections += a.get("collections", 0) - b.get("collections", 0)
            gc_collected += a.get("collected", 0) - b.get("collected", 0)

        # Line hotspots — top 20 by total time
        line_totals: List[Tuple[Tuple[str, int], int, int]] = []
        for key, durations in self.line_timings.items():
            total = sum(durations)
            count = len(durations)
            line_totals.append((key, total, count))
        line_totals.sort(key=lambda x: x[1], reverse=True)
        line_hotspots = [
            {
                "filename": k[0],
                "lineno": k[1],
                "total_ns": t,
                "hit_count": c,
                "avg_ns": t // c if c else 0,
            }
            for k, t, c in line_totals[:20]
        ]

        # Function hotspots — top 20 by total_ns
        func_list = [
            (name, info["total_ns"], info["calls"])
            for name, info in self.call_profile.items()
        ]
        func_list.sort(key=lambda x: x[1], reverse=True)
        function_hotspots = [
            {
                "function": name,
                "total_ns": tns,
                "calls": calls,
                "avg_ns": tns // calls if calls else 0,
            }
            for name, tns, calls in func_list[:20]
        ]

        # Memory timeline (convert tuples to dicts)
        memory_timeline = [
            {
                "timestamp_ns": ts,
                "current_bytes": cur,
                "peak_bytes": peak,
            }
            for ts, cur, peak in self.memory_snapshots
        ]

        # I/O timeline (convert tuples to dicts)
        io_timeline = [
            {
                "timestamp_ns": ts,
                "operation": op,
                "byte_count": nbytes,
                "cumulative_bytes": cum,
            }
            for ts, op, nbytes, cum in self.io_operations
        ]

        # Call graph (serialize sets for JSON compat)
        call_graph = {
            name: {
                "calls": info["calls"],
                "total_ns": info["total_ns"],
                "children": sorted(info["children"]),
            }
            for name, info in self.call_profile.items()
        }

        return {
            # Timing
            "total_wall_ns": total_wall_ns,
            "total_cpu_ns": total_cpu_ns,
            # Execution volume
            "total_lines_executed": total_lines,
            "total_bytecode_ops": total_bytecodes,
            # Memory
            "peak_memory_bytes": peak_mem,
            # I/O
            "total_io_calls": total_io_calls,
            "total_io_bytes": total_io_bytes,
            # Call profiling
            "peak_call_depth": self._peak_depth,
            "total_function_calls": total_func_calls,
            "unique_functions": unique_funcs,
            # Breakdowns
            "bytecode_breakdown": dict(agg_bytecode),
            "memory_timeline": memory_timeline,
            "io_timeline": io_timeline,
            "line_hotspots": line_hotspots,
            "function_hotspots": function_hotspots,
            "call_graph": call_graph,
            # GC
            "gc_collections": gc_collections,
            "gc_collected_objects": gc_collected,
            # Raw counts for further analysis
            "total_events": len(self.events),
            "event_breakdown": {
                "line": total_lines,
                "call": sum(1 for e in self.events if e[_EVT_TYPE] == "call"),
                "return": sum(1 for e in self.events if e[_EVT_TYPE] == "return"),
                "exception": sum(1 for e in self.events if e[_EVT_TYPE] == "exception"),
            },
        }

    # ----- convenience accessors ------------------------------------------

    def events_as_dicts(self) -> List[dict]:
        """Convert the raw event tuples into a list of dictionaries."""
        out: List[dict] = []
        for ev in self.events:
            d: dict = {
                "event_type": ev[_EVT_TYPE],
                "timestamp_ns": ev[_EVT_TS],
                "filename": ev[_EVT_FILE],
                "lineno": ev[_EVT_LINE],
                "function_name": ev[_EVT_FUNC],
            }
            if ev[_EVT_TYPE] == "return":
                d["return_value_type"] = ev[_EVT_EXTRA]
            elif ev[_EVT_TYPE] == "exception" and ev[_EVT_EXTRA]:
                d["exception_type"] = ev[_EVT_EXTRA][0]
                d["exception_message"] = ev[_EVT_EXTRA][1]
            out.append(d)
        return out


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

def trace(code_str: str, globals_ns: Optional[dict] = None) -> dict:
    """One-shot helper: trace *code_str* and return the summary dict."""
    tracer = DeepTracer()
    return tracer.trace_execution(code_str, globals_ns)
