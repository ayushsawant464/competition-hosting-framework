#!/usr/bin/env python3
"""
Deep Execution Comparison: Real Hardware vs Virtual Emulation
=============================================================

This script performs the most thorough possible comparison between:
  1. REAL execution — using sys.settrace, tracemalloc, dis, time.perf_counter_ns()
  2. VIRTUAL execution — using our BenchmarkEnvironment emulation framework

It records everything at the finest granularity and produces a detailed
report identifying:
  - Which hardware calls are made during execution
  - Which ones our emulation successfully virtualizes
  - Where the values diverge and by how much
  - Calibration factors needed to close the gap
"""

import sys
import os
import time
import gc
import dis
import json
import csv
import math
import tracemalloc
import types
from pathlib import Path
from collections import defaultdict
from io import StringIO

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from emu.environment import BenchmarkEnvironment
from emu.virtual_network import InMemoryConnection
from emu.metrics import MetricsCalculator


# ═══════════════════════════════════════════════════════════════════════════
# PART 1: Deep Real Execution Tracer
# ═══════════════════════════════════════════════════════════════════════════

class RealExecutionTracer:
    """Records everything that happens during actual Python execution."""

    # Bytecode instruction categories
    OPCODE_CATEGORIES = {
        'name_lookups': {
            'LOAD_FAST', 'LOAD_FAST_CHECK', 'LOAD_FAST_AND_CLEAR',
            'LOAD_GLOBAL', 'LOAD_NAME', 'LOAD_DEREF', 'LOAD_CLOSURE',
            'STORE_FAST', 'STORE_GLOBAL', 'STORE_NAME', 'STORE_DEREF',
            'DELETE_FAST', 'DELETE_GLOBAL', 'DELETE_NAME', 'DELETE_DEREF',
            'LOAD_CONST', 'LOAD_FAST_LOAD_FAST', 'STORE_FAST_STORE_FAST',
        },
        'arithmetic': {
            'BINARY_OP', 'COMPARE_OP', 'UNARY_NOT', 'UNARY_NEGATIVE',
            'UNARY_INVERT', 'IS_OP', 'CONTAINS_OP',
        },
        'control_flow': {
            'FOR_ITER', 'JUMP_FORWARD', 'JUMP_BACKWARD',
            'POP_JUMP_IF_TRUE', 'POP_JUMP_IF_FALSE',
            'POP_JUMP_IF_NONE', 'POP_JUMP_IF_NOT_NONE',
            'JUMP_BACKWARD_NO_INTERRUPT', 'GET_ITER',
            'JUMP_IF_TRUE_OR_POP', 'JUMP_IF_FALSE_OR_POP',
            'JUMP', 'JUMP_NO_INTERRUPT',
        },
        'function_calls': {
            'CALL', 'CALL_FUNCTION', 'CALL_METHOD', 'CALL_FUNCTION_EX',
            'CALL_KW', 'PUSH_NULL', 'KW_NAMES',
            'CALL_INTRINSIC_1', 'CALL_INTRINSIC_2',
        },
        'data_access': {
            'LOAD_ATTR', 'STORE_ATTR', 'DELETE_ATTR',
            'BINARY_SUBSCR', 'STORE_SUBSCR', 'DELETE_SUBSCR',
            'LOAD_SUPER_ATTR',
        },
        'stack_ops': {
            'POP_TOP', 'COPY', 'SWAP', 'NOP', 'RESUME',
            'PUSH_EXC_INFO', 'POP_EXCEPT', 'RERAISE',
            'CHECK_EXC_MATCH', 'CHECK_EG_MATCH',
        },
        'build_ops': {
            'BUILD_LIST', 'BUILD_TUPLE', 'BUILD_SET', 'BUILD_MAP',
            'BUILD_CONST_KEY_MAP', 'BUILD_STRING', 'BUILD_SLICE',
            'LIST_APPEND', 'SET_ADD', 'MAP_ADD', 'LIST_EXTEND',
            'SET_UPDATE', 'DICT_UPDATE', 'DICT_MERGE',
            'UNPACK_SEQUENCE', 'UNPACK_EX',
        },
        'return_yield': {
            'RETURN_VALUE', 'RETURN_CONST', 'YIELD_VALUE',
            'GET_YIELD_FROM_ITER', 'SEND', 'END_FOR',
        },
        'import_ops': {
            'IMPORT_NAME', 'IMPORT_FROM', 'IMPORT_STAR',
        },
        'format_ops': {
            'FORMAT_VALUE', 'FORMAT_SIMPLE',
        },
    }

    def __init__(self):
        self._total_events = 0
        self.memory_snapshots = [] # (timestamp_ns, current_bytes, peak_bytes)
        self.io_operations = []    # (timestamp_ns, op_name, byte_count, cumulative_in, cumulative_out)
        self.line_timings = defaultdict(list)  # (filename, lineno) -> [duration_ns]
        self.call_profile = defaultdict(lambda: {'calls': 0, 'total_ns': 0, 'children': set()})
        self.bytecode_profile = defaultdict(lambda: defaultdict(int))
        self.gc_before = None
        self.gc_after = None

        # Runtime state
        self._call_stack = []      # (func_name, entry_ns)
        self._last_line_time = None
        self._last_line_key = None
        self._io_bytes_in = 0
        self._io_bytes_out = 0
        self._total_lines = 0
        self._peak_depth = 0
        self._start_wall_ns = 0
        self._end_wall_ns = 0
        self._start_cpu_ns = 0
        self._end_cpu_ns = 0

    def _trace_callback(self, frame, event, arg):
        """sys.settrace callback — records every event."""
        now = time.perf_counter_ns()
        filename = frame.f_code.co_filename
        lineno = frame.f_lineno
        func_name = frame.f_code.co_qualname if hasattr(frame.f_code, 'co_qualname') else frame.f_code.co_name

        # Only trace user code, not stdlib/emu internals
        if '<string>' not in filename and '<benchmark>' not in filename and 'run_deep' not in filename:
            return self._trace_callback

        if event == 'line':
            self._total_lines += 1
            # Record duration of previous line
            if self._last_line_time is not None and self._last_line_key is not None:
                duration = now - self._last_line_time
                self.line_timings[self._last_line_key].append(duration)
            self._last_line_time = now
            self._last_line_key = (filename, lineno)

        elif event == 'call':
            self._call_stack.append((func_name, now))
            depth = len(self._call_stack)
            if depth > self._peak_depth:
                self._peak_depth = depth
            self.call_profile[func_name]['calls'] += 1
            # Track call graph
            if len(self._call_stack) >= 2:
                caller = self._call_stack[-2][0]
                self.call_profile[caller]['children'].add(func_name)

        elif event == 'return':
            if self._call_stack:
                fn, entry_ns = self._call_stack.pop()
                duration = now - entry_ns
                self.call_profile[fn]['total_ns'] += duration

        # We no longer store every event to prevent memory bloat
        # self.events.append(...)
        self._total_events += 1

        return self._trace_callback

    def _count_bytecode_ops(self, code_obj, visited=None):
        """Recursively count bytecode operations in a code object tree."""
        if visited is None:
            visited = set()
        if id(code_obj) in visited:
            return
        visited.add(id(code_obj))

        func_name = code_obj.co_qualname if hasattr(code_obj, 'co_qualname') else code_obj.co_name
        category_counts = defaultdict(int)
        total = 0

        try:
            for instr in dis.get_instructions(code_obj):
                total += 1
                categorized = False
                for cat_name, opcodes in self.OPCODE_CATEGORIES.items():
                    if instr.opname in opcodes:
                        category_counts[cat_name] += 1
                        categorized = True
                        break
                if not categorized:
                    category_counts['other'] += 1
        except Exception:
            pass

        if total > 0:
            category_counts['total'] = total
            self.bytecode_profile[func_name] = dict(category_counts)

        # Recurse into nested code objects
        for const in code_obj.co_consts:
            if isinstance(const, types.CodeType):
                self._count_bytecode_ops(const, visited)

    def trace_execution(self, code_str, globals_ns=None, input_bytes=b""):
        """Execute code with full tracing. Returns summary dict."""
        if globals_ns is None:
            globals_ns = {}

        # Pre-analyze bytecode
        compiled = compile(code_str, '<string>', 'exec')
        self._count_bytecode_ops(compiled)

        # GC stats before
        gc.collect()
        self.gc_before = gc.get_stats()

        # Start memory tracking
        tracemalloc.start(25)

        # Record start times
        self._start_wall_ns = time.perf_counter_ns()
        self._start_cpu_ns = time.process_time_ns()

        # Install trace hook
        sys.settrace(self._trace_callback)

        error = None
        try:
            exec(compiled, globals_ns)
        except Exception as e:
            error = e
        finally:
            sys.settrace(None)

        # Record end times
        self._end_wall_ns = time.perf_counter_ns()
        self._end_cpu_ns = time.process_time_ns()

        # Final memory snapshot
        snapshot = tracemalloc.take_snapshot()
        current, peak = tracemalloc.get_traced_memory()
        self.memory_snapshots.append((self._end_wall_ns, current, peak))
        tracemalloc.stop()

        # GC stats after
        gc.collect()
        self.gc_after = gc.get_stats()

        # Close out last line timing
        if self._last_line_time is not None and self._last_line_key is not None:
            duration = self._end_wall_ns - self._last_line_time
            self.line_timings[self._last_line_key].append(duration)

        return self.get_summary(error)

    def get_summary(self, error=None):
        """Build comprehensive summary of all traced metrics."""
        wall_ns = self._end_wall_ns - self._start_wall_ns
        cpu_ns = self._end_cpu_ns - self._start_cpu_ns

        # Aggregate bytecode counts
        total_bytecode = 0
        bytecode_breakdown = defaultdict(int)
        for func_name, cats in self.bytecode_profile.items():
            for cat, count in cats.items():
                if cat != 'total':
                    bytecode_breakdown[cat] += count
                else:
                    total_bytecode += count

        # Peak memory from tracemalloc
        peak_memory_bytes = 0
        if self.memory_snapshots:
            peak_memory_bytes = max(s[2] for s in self.memory_snapshots)

        # Line hotspots
        line_hotspots = []
        for (filename, lineno), durations in self.line_timings.items():
            total = sum(durations)
            count = len(durations)
            line_hotspots.append({
                'file': filename, 'line': lineno,
                'total_ns': total, 'count': count,
                'avg_ns': total / count if count else 0,
            })
        line_hotspots.sort(key=lambda x: x['total_ns'], reverse=True)

        # Function hotspots
        function_hotspots = []
        for func_name, prof in self.call_profile.items():
            function_hotspots.append({
                'function': func_name,
                'calls': prof['calls'],
                'total_ns': prof['total_ns'],
                'avg_ns': prof['total_ns'] / prof['calls'] if prof['calls'] else 0,
                'children': list(prof['children']),
            })
        function_hotspots.sort(key=lambda x: x['total_ns'], reverse=True)

        # GC collections
        gc_collections = 0
        gc_collected = 0
        if self.gc_before and self.gc_after:
            for gen_before, gen_after in zip(self.gc_before, self.gc_after):
                gc_collections += gen_after.get('collections', 0) - gen_before.get('collections', 0)
                gc_collected += gen_after.get('collected', 0) - gen_before.get('collected', 0)

        return {
            # Timing
            'total_wall_ns': wall_ns,
            'total_cpu_ns': cpu_ns,
            'wall_time_ms': wall_ns / 1_000_000,
            'cpu_time_ms': cpu_ns / 1_000_000,

            # Lines
            'total_lines_executed': self._total_lines,

            # Bytecode
            'total_bytecode_ops': total_bytecode,
            'bytecode_breakdown': dict(bytecode_breakdown),

            # Memory
            'peak_memory_bytes': peak_memory_bytes,
            'peak_memory_mb': peak_memory_bytes / (1024 * 1024),
            'memory_snapshots': len(self.memory_snapshots),

            # I/O
            'total_io_calls': len(self.io_operations),
            'total_io_bytes_in': self._io_bytes_in,
            'total_io_bytes_out': self._io_bytes_out,

            # Call stack
            'peak_call_depth': self._peak_depth,
            'total_function_calls': sum(p['calls'] for p in self.call_profile.values()),
            'unique_functions': len(self.call_profile),

            # GC
            'gc_collections': gc_collections,
            'gc_collected_objects': gc_collected,

            # Hotspots
            'line_hotspots': line_hotspots[:20],
            'function_hotspots': function_hotspots[:20],

            # Bytecode per function
            'bytecode_per_function': dict(self.bytecode_profile),

            # Error
            'error': str(error) if error else None,
            'total_events': self._total_events,
        }


# ═══════════════════════════════════════════════════════════════════════════
# PART 2: Virtual Execution Tracer (hooks into our emulation framework)
# ═══════════════════════════════════════════════════════════════════════════

class VirtualExecutionTracer:
    """Records everything from the emulation framework execution."""

    def __init__(self):
        self.cycle_log = []      # List of (event, cycles_charged, source)
        self.mem_log = []        # List of (event, name, size_bytes)
        self.frame_log = []      # List of (event, func_name, depth)
        self.io_log = []         # List of (event, op, bytes, cycles_charged)
        self._start_ns = 0
        self._end_ns = 0

    def run_virtual(self, code_str, input_bytes):
        """Run code through emulation framework with logging."""
        from emu.virtual_network import InMemoryConnection

        env = BenchmarkEnvironment(memory_limit_mb=512)
        mock_conn = InMemoryConnection(
            env.state,
            input_bytes=input_bytes,
            clock=env.state.clock
        )
        env.preloaded_conn = mock_conn
        env.state.preloaded_conn = mock_conn

        # Instrument the EmulationState to log events
        original_increment = env.state.increment.__func__
        original_track_mem = env.state.track_mem.__func__
        original_push_frame = env.state.push_frame.__func__
        original_pop_frame = env.state.pop_frame.__func__

        tracer = self

        def logged_increment(self_state, amount):
            tracer.cycle_log.append(('increment', amount, self_state.virtual_cycles))
            original_increment(self_state, amount)

        def logged_track_mem(self_state, name, obj):
            old_size = self_state.memory.allocations.get(name, 0)
            original_track_mem(self_state, name, obj)
            new_size = self_state.memory.allocations.get(name, 0)
            tracer.mem_log.append(('track', name, new_size, new_size - old_size))

        def logged_push_frame(self_state, func_name, n_locals=0):
            original_push_frame(self_state, func_name, n_locals)
            depth = self_state.frame_tracker.current_depth
            tracer.frame_log.append(('push', func_name, depth))

        def logged_pop_frame(self_state):
            if self_state.frame_tracker.stack:
                func_name = self_state.frame_tracker.stack[-1][0]
            else:
                func_name = '?'
            original_pop_frame(self_state)
            depth = self_state.frame_tracker.current_depth
            tracer.frame_log.append(('pop', func_name, depth))

        # Monkey-patch
        import types as t
        env.state.increment = t.MethodType(logged_increment, env.state)
        env.state.track_mem = t.MethodType(logged_track_mem, env.state)
        env.state.push_frame = t.MethodType(logged_push_frame, env.state)
        env.state.pop_frame = t.MethodType(logged_pop_frame, env.state)

        # Also instrument I/O
        original_recv = mock_conn.recv
        original_send = mock_conn.send
        original_sendall = mock_conn.sendall

        def logged_recv(bufsize, flags=0):
            data = original_recv(bufsize, flags)
            tracer.io_log.append(('recv', len(data), mock_conn.recv_count))
            return data

        def logged_send(data, flags=0):
            result = original_send(data, flags)
            tracer.io_log.append(('send', len(data), 0))
            return result

        def logged_sendall(data, flags=0):
            result = original_sendall(data, flags)
            tracer.io_log.append(('sendall', len(data), 0))
            return result

        mock_conn.recv = logged_recv
        mock_conn.send = logged_send
        mock_conn.sendall = logged_sendall

        self._start_ns = time.perf_counter_ns()
        result = env.run(code_str)
        self._end_ns = time.perf_counter_ns()

        return self._build_summary(result, env, mock_conn)

    def _build_summary(self, result, env, mock_conn):
        """Build virtual execution summary."""
        wall_ns = self._end_ns - self._start_ns
        frame_profile = env.state.frame_tracker.get_profile()

        # Aggregate cycle events
        total_cycles = result.virtual_cycles
        increment_count = len(self.cycle_log)

        # Aggregate memory events
        mem_events = len(self.mem_log)
        named_allocations = {}
        for event, name, size, delta in self.mem_log:
            named_allocations[name] = size

        # Aggregate I/O
        recv_count = sum(1 for e in self.io_log if e[0] == 'recv')
        send_count = sum(1 for e in self.io_log if e[0] in ('send', 'sendall'))
        recv_bytes = sum(e[1] for e in self.io_log if e[0] == 'recv')
        send_bytes = sum(e[1] for e in self.io_log if e[0] in ('send', 'sendall'))

        # Frame events
        push_count = sum(1 for e in self.frame_log if e[0] == 'push')
        pop_count = sum(1 for e in self.frame_log if e[0] == 'pop')
        peak_depth = frame_profile['peak_depth']

        # Function call counts from frame tracker
        call_counts = frame_profile.get('call_counts', {})

        # Cycle breakdown by source (approximate)
        cycle_categories = {
            'statement_costs': 0,
            'memory_costs': 0,
            'io_costs': 0,
            'frame_costs': 0,
        }
        # We can estimate from the logs
        io_cycle_total = recv_count * 500 + send_count * 200  # From InMemoryConnection
        cycle_categories['io_costs'] = io_cycle_total
        cycle_categories['statement_costs'] = total_cycles - io_cycle_total
        if cycle_categories['statement_costs'] < 0:
            cycle_categories['statement_costs'] = 0

        return {
            # Timing
            'total_wall_ns': wall_ns,
            'wall_time_ms': wall_ns / 1_000_000,

            # Virtual cycles
            'total_virtual_cycles': total_cycles,
            'virtual_time_ns': result.clock.current_ns,
            'virtual_time_ms': result.clock.current_ns / 1_000_000,
            'increment_count': increment_count,

            # Memory
            'peak_memory_mb': result.peak_memory_mb,
            'peak_memory_bytes': int(result.peak_memory_mb * 1024 * 1024),
            'named_allocations': len(named_allocations),
            'memory_events': mem_events,
            'memory_timeline_samples': len(result.memory_tracker.timeline),

            # I/O
            'recv_calls': recv_count,
            'send_calls': send_count,
            'recv_bytes': recv_bytes,
            'send_bytes': send_bytes,

            # Call stack
            'total_function_calls': push_count,
            'peak_call_depth': peak_depth,
            'unique_functions': frame_profile['unique_functions'],
            'function_call_counts': call_counts,

            # Frame tracking
            'peak_frame_memory_bytes': frame_profile['peak_frame_memory_bytes'],
            'peak_frame_memory_mb': frame_profile['peak_frame_memory_mb'],

            # Cycle breakdown
            'cycle_breakdown': cycle_categories,

            # Latency
            'order_latencies': mock_conn.order_latencies,
            'avg_latency_ns': (sum(mock_conn.order_latencies) / len(mock_conn.order_latencies))
                              if mock_conn.order_latencies else 0,

            # Error
            'error': str(result.error) if result.error else None,
        }


# ═══════════════════════════════════════════════════════════════════════════
# PART 3: Side-by-Side Comparison Engine
# ═══════════════════════════════════════════════════════════════════════════

def compare_executions(real_summary, virtual_summary):
    """Compare real vs virtual execution summaries. Returns comparison report."""

    def ratio(a, b):
        """Compute a/b ratio, handling zeros."""
        if b == 0:
            return float('inf') if a > 0 else 1.0
        return a / b

    def pct_diff(a, b):
        """Percentage difference: (a - b) / b * 100."""
        if b == 0:
            return float('inf') if a > 0 else 0.0
        return ((a - b) / b) * 100

    report = {
        'hardware_calls': {},
        'metrics_comparison': {},
        'virtualization_coverage': {},
        'calibration_suggestions': {},
    }

    # ─── 1. Hardware Call Comparison ───────────────────────────────
    hw_calls = report['hardware_calls']

    # CPU execution
    hw_calls['cpu_execution'] = {
        'real': {
            'wall_time_ms': real_summary['wall_time_ms'],
            'cpu_time_ms': real_summary['cpu_time_ms'],
            'total_lines': real_summary['total_lines_executed'],
            'bytecode_ops': real_summary['total_bytecode_ops'],
        },
        'virtual': {
            'virtual_cycles': virtual_summary['total_virtual_cycles'],
            'virtual_time_ns': virtual_summary['virtual_time_ns'],
            'virtual_time_ms': virtual_summary['virtual_time_ms'],
            'increment_calls': virtual_summary['increment_count'],
        },
        'status': 'VIRTUALIZED',
        'notes': 'CPU time replaced with deterministic cycle counting',
    }

    # Memory allocation
    hw_calls['memory_allocation'] = {
        'real': {
            'peak_bytes': real_summary['peak_memory_bytes'],
            'peak_mb': real_summary['peak_memory_mb'],
        },
        'virtual': {
            'peak_bytes': virtual_summary['peak_memory_bytes'],
            'peak_mb': virtual_summary['peak_memory_mb'],
            'named_allocations': virtual_summary['named_allocations'],
            'memory_events': virtual_summary['memory_events'],
        },
        'ratio': ratio(virtual_summary['peak_memory_bytes'], real_summary['peak_memory_bytes'])
                 if real_summary['peak_memory_bytes'] > 0 else 'N/A',
        'status': 'VIRTUALIZED',
        'notes': 'Structural size estimation replaces tracemalloc',
    }

    # Network I/O
    real_io_calls = real_summary.get('total_io_calls', 0)
    virtual_recv = virtual_summary.get('recv_calls', 0)
    virtual_send = virtual_summary.get('send_calls', 0)
    hw_calls['network_io'] = {
        'real': {
            'total_io_calls': real_io_calls,
        },
        'virtual': {
            'recv_calls': virtual_recv,
            'send_calls': virtual_send,
            'total_io_calls': virtual_recv + virtual_send,
            'recv_bytes': virtual_summary.get('recv_bytes', 0),
            'send_bytes': virtual_summary.get('send_bytes', 0),
        },
        'status': 'VIRTUALIZED',
        'notes': 'InMemoryConnection replaces real socket ops with deterministic fragmentation',
    }

    # Function call stack
    hw_calls['call_stack'] = {
        'real': {
            'total_calls': real_summary['total_function_calls'],
            'peak_depth': real_summary['peak_call_depth'],
            'unique_functions': real_summary['unique_functions'],
        },
        'virtual': {
            'total_calls': virtual_summary['total_function_calls'],
            'peak_depth': virtual_summary['peak_call_depth'],
            'unique_functions': virtual_summary['unique_functions'],
        },
        'call_ratio': ratio(virtual_summary['total_function_calls'],
                           real_summary['total_function_calls']),
        'status': 'VIRTUALIZED',
        'notes': 'FrameTracker replaces real Python frame objects',
    }

    # Garbage collection
    hw_calls['garbage_collection'] = {
        'real': {
            'gc_collections': real_summary['gc_collections'],
            'gc_collected': real_summary['gc_collected_objects'],
        },
        'virtual': {
            'gc_model': 'NOT IMPLEMENTED',
            'notes': 'GC is not modeled in virtual environment',
        },
        'status': 'NOT_VIRTUALIZED',
        'notes': 'GC pauses are not emulated — potential accuracy gap',
    }

    # File I/O (disk access)
    hw_calls['disk_io'] = {
        'real': {
            'notes': 'CSV data loaded from disk before execution',
        },
        'virtual': {
            'notes': 'Data pre-loaded into InMemoryConnection bytes',
        },
        'status': 'VIRTUALIZED',
        'notes': 'File I/O replaced with in-memory byte buffer',
    }

    # ─── 2. Metrics Comparison ────────────────────────────────────
    metrics = report['metrics_comparison']

    # Memory comparison
    if real_summary['peak_memory_bytes'] > 0:
        mem_ratio = virtual_summary['peak_memory_bytes'] / real_summary['peak_memory_bytes']
    else:
        mem_ratio = 0
    metrics['memory'] = {
        'real_peak_mb': real_summary['peak_memory_mb'],
        'virtual_peak_mb': virtual_summary['peak_memory_mb'],
        'ratio': mem_ratio,
        'pct_diff': pct_diff(virtual_summary['peak_memory_bytes'],
                            real_summary['peak_memory_bytes']),
        'accuracy': 'GOOD' if 0.5 <= mem_ratio <= 2.0 else 'NEEDS_CALIBRATION',
    }

    # Call count comparison
    if real_summary['total_function_calls'] > 0:
        call_ratio = virtual_summary['total_function_calls'] / real_summary['total_function_calls']
    else:
        call_ratio = 0
    metrics['function_calls'] = {
        'real_total': real_summary['total_function_calls'],
        'virtual_total': virtual_summary['total_function_calls'],
        'ratio': call_ratio,
        'real_depth': real_summary['peak_call_depth'],
        'virtual_depth': virtual_summary['peak_call_depth'],
    }

    # I/O comparison
    metrics['io'] = {
        'virtual_recv_calls': virtual_recv,
        'virtual_send_calls': virtual_send,
        'virtual_recv_bytes': virtual_summary.get('recv_bytes', 0),
        'virtual_send_bytes': virtual_summary.get('send_bytes', 0),
    }

    # ─── 3. Virtualization Coverage ───────────────────────────────
    coverage = report['virtualization_coverage']
    all_calls = ['cpu_execution', 'memory_allocation', 'network_io',
                 'call_stack', 'garbage_collection', 'disk_io']
    virtualized = sum(1 for c in all_calls if hw_calls[c]['status'] == 'VIRTUALIZED')
    coverage['total_hardware_categories'] = len(all_calls)
    coverage['virtualized'] = virtualized
    coverage['not_virtualized'] = len(all_calls) - virtualized
    coverage['coverage_pct'] = (virtualized / len(all_calls)) * 100
    coverage['gaps'] = [c for c in all_calls if hw_calls[c]['status'] != 'VIRTUALIZED']

    # ─── 4. Calibration Suggestions ──────────────────────────────
    calibrations = report['calibration_suggestions']

    if real_summary['peak_memory_bytes'] > 0:
        calibrations['memory_scale_factor'] = real_summary['peak_memory_bytes'] / max(1, virtual_summary['peak_memory_bytes'])
        if not (0.5 <= mem_ratio <= 2.0):
            calibrations['memory_action'] = (
                f"Virtual memory is {mem_ratio:.2f}x real. "
                f"Adjust structural size estimates or add GC overhead model."
            )

    # Bytecode-to-cycle mapping
    if real_summary['total_bytecode_ops'] > 0 and virtual_summary['total_virtual_cycles'] > 0:
        cycles_per_bytecode = virtual_summary['total_virtual_cycles'] / real_summary['total_bytecode_ops']
        calibrations['cycles_per_bytecode_op'] = cycles_per_bytecode
        calibrations['bytecode_ops_total'] = real_summary['total_bytecode_ops']
        ns_per_bytecode = real_summary['total_cpu_ns'] / real_summary['total_bytecode_ops']
        calibrations['real_ns_per_bytecode_op'] = ns_per_bytecode
        calibrations['virtual_ns_per_cycle'] = 1.0 / 3.0  # From VirtualClock (3 GHz)
        calibrations['ideal_cycles_per_bytecode'] = ns_per_bytecode / (1.0 / 3.0)

    return report


# ═══════════════════════════════════════════════════════════════════════════
# PART 4: Report Formatter
# ═══════════════════════════════════════════════════════════════════════════

def format_report(comparison, real_summary, virtual_summary):
    """Format comparison as a detailed text report."""
    lines = []
    W = 80

    lines.append("=" * W)
    lines.append("  DEEP EXECUTION COMPARISON: REAL vs VIRTUAL")
    lines.append("=" * W)
    lines.append("")

    # ─── Hardware Calls ───
    lines.append("┌" + "─" * (W-2) + "┐")
    lines.append("│  HARDWARE CALL VIRTUALIZATION STATUS" + " " * (W - 40) + "│")
    lines.append("├" + "─" * (W-2) + "┤")

    for category, info in comparison['hardware_calls'].items():
        status = info.get('status', 'UNKNOWN')
        icon = "✅" if status == 'VIRTUALIZED' else "❌"
        name = category.replace('_', ' ').title()
        line = f"│  {icon} {name:<30} [{status}]"
        lines.append(line.ljust(W-1) + "│")
        if 'notes' in info:
            note = f"│     └─ {info['notes']}"
            lines.append(note[:W-1].ljust(W-1) + "│")

    lines.append("└" + "─" * (W-2) + "┘")

    coverage = comparison['virtualization_coverage']
    lines.append(f"\n  Coverage: {coverage['virtualized']}/{coverage['total_hardware_categories']} "
                 f"({coverage['coverage_pct']:.0f}%) hardware categories virtualized")
    if coverage['gaps']:
        lines.append(f"  Gaps: {', '.join(coverage['gaps'])}")
    lines.append("")

    # ─── Side-by-Side Metrics ───
    lines.append("┌" + "─" * (W-2) + "┐")
    lines.append("│  SIDE-BY-SIDE METRICS COMPARISON" + " " * (W - 36) + "│")
    lines.append("├" + "─" * 35 + "┬" + "─" * 20 + "┬" + "─" * (W-59) + "┤")
    lines.append(f"│ {'Metric':<33} │ {'Real':>18} │ {'Virtual':>{W-60}} │")
    lines.append("├" + "─" * 35 + "┼" + "─" * 20 + "┼" + "─" * (W-59) + "┤")

    metric_rows = [
        ("Wall time (ms)", f"{real_summary['wall_time_ms']:.2f}",
         f"{virtual_summary['wall_time_ms']:.2f}"),
        ("CPU time (ms)", f"{real_summary['cpu_time_ms']:.2f}", "N/A (virtual)"),
        ("Virtual cycles", "N/A (real)", f"{virtual_summary['total_virtual_cycles']:,}"),
        ("Virtual time (ns)", "N/A (real)", f"{virtual_summary['virtual_time_ns']:,}"),
        ("Peak memory (MB)", f"{real_summary['peak_memory_mb']:.3f}",
         f"{virtual_summary['peak_memory_mb']:.3f}"),
        ("Lines executed", f"{real_summary['total_lines_executed']:,}", "N/A"),
        ("Bytecode ops (static)", f"{real_summary['total_bytecode_ops']:,}", "N/A"),
        ("Function calls", f"{real_summary['total_function_calls']:,}",
         f"{virtual_summary['total_function_calls']:,}"),
        ("Peak call depth", f"{real_summary['peak_call_depth']}",
         f"{virtual_summary['peak_call_depth']}"),
        ("Unique functions", f"{real_summary['unique_functions']}",
         f"{virtual_summary['unique_functions']}"),
        ("I/O recv calls", f"{real_summary.get('real_recv_calls', 'N/A'):,}" if isinstance(real_summary.get('real_recv_calls'), int) else "N/A",
         f"{virtual_summary.get('recv_calls', 0):,}"),
        ("I/O send calls", f"{real_summary.get('real_send_calls', 'N/A'):,}" if isinstance(real_summary.get('real_send_calls'), int) else "N/A",
         f"{virtual_summary.get('send_calls', 0):,}"),
        ("I/O recv bytes", f"{real_summary.get('real_recv_bytes', 'N/A'):,}" if isinstance(real_summary.get('real_recv_bytes'), int) else "N/A",
         f"{virtual_summary.get('recv_bytes', 0):,}"),
        ("I/O send bytes", f"{real_summary.get('real_send_bytes', 'N/A'):,}" if isinstance(real_summary.get('real_send_bytes'), int) else "N/A",
         f"{virtual_summary.get('send_bytes', 0):,}"),
        ("GC collections", f"{real_summary['gc_collections']}", "Not modeled"),
        ("GC objects collected", f"{real_summary['gc_collected_objects']}", "Not modeled"),
    ]

    for label, real_val, virt_val in metric_rows:
        lines.append(f"│ {label:<33} │ {real_val:>18} │ {virt_val:>{W-60}} │")

    lines.append("└" + "─" * 35 + "┴" + "─" * 20 + "┴" + "─" * (W-59) + "┘")
    lines.append("")

    # ─── Memory Accuracy ───
    mem = comparison['metrics_comparison']['memory']
    lines.append("┌" + "─" * (W-2) + "┐")
    lines.append("│  MEMORY ACCURACY" + " " * (W - 20) + "│")
    lines.append("├" + "─" * (W-2) + "┤")
    lines.append(f"│  Real peak:    {mem['real_peak_mb']:.3f} MB".ljust(W-1) + "│")
    lines.append(f"│  Virtual peak: {mem['virtual_peak_mb']:.3f} MB".ljust(W-1) + "│")
    lines.append(f"│  Ratio:        {mem['ratio']:.3f}x (virtual/real)".ljust(W-1) + "│")
    lines.append(f"│  % Difference: {mem['pct_diff']:.1f}%".ljust(W-1) + "│")
    lines.append(f"│  Accuracy:     {mem['accuracy']}".ljust(W-1) + "│")
    lines.append("└" + "─" * (W-2) + "┘")
    lines.append("")

    # ─── Bytecode Analysis ───
    if real_summary['bytecode_breakdown']:
        lines.append("┌" + "─" * (W-2) + "┐")
        lines.append("│  BYTECODE INSTRUCTION BREAKDOWN (static analysis)" + " " * (W - 53) + "│")
        lines.append("├" + "─" * (W-2) + "┤")
        for cat, count in sorted(real_summary['bytecode_breakdown'].items(),
                                  key=lambda x: -x[1]):
            pct = (count / real_summary['total_bytecode_ops'] * 100) if real_summary['total_bytecode_ops'] > 0 else 0
            bar = "█" * int(pct / 2)
            line = f"│  {cat:<20} {count:>6} ({pct:5.1f}%) {bar}"
            lines.append(line[:W-1].ljust(W-1) + "│")
        lines.append("└" + "─" * (W-2) + "┘")
        lines.append("")

    # ─── Calibration Suggestions ───
    cal = comparison.get('calibration_suggestions', {})
    if cal:
        lines.append("┌" + "─" * (W-2) + "┐")
        lines.append("│  CALIBRATION INSIGHTS" + " " * (W - 25) + "│")
        lines.append("├" + "─" * (W-2) + "┤")
        if 'cycles_per_bytecode_op' in cal:
            lines.append(f"│  Virtual cycles per bytecode op: {cal['cycles_per_bytecode_op']:.2f}".ljust(W-1) + "│")
        if 'real_ns_per_bytecode_op' in cal:
            lines.append(f"│  Real ns per bytecode op:        {cal['real_ns_per_bytecode_op']:.2f}".ljust(W-1) + "│")
        if 'ideal_cycles_per_bytecode' in cal:
            lines.append(f"│  Ideal cycles/bytecode at 3GHz:  {cal['ideal_cycles_per_bytecode']:.2f}".ljust(W-1) + "│")
        if 'memory_scale_factor' in cal:
            lines.append(f"│  Memory scale factor (real/virt): {cal['memory_scale_factor']:.3f}".ljust(W-1) + "│")
        if 'memory_action' in cal:
            lines.append(f"│  ⚠ {cal['memory_action']}"[:W-1].ljust(W-1) + "│")
        lines.append("└" + "─" * (W-2) + "┘")
        lines.append("")

    # ─── Function Hotspots (Real) ───
    if real_summary['function_hotspots']:
        lines.append("┌" + "─" * (W-2) + "┐")
        lines.append("│  TOP FUNCTION HOTSPOTS (Real Execution)" + " " * (W - 43) + "│")
        lines.append("├" + "─" * (W-2) + "┤")
        for i, hs in enumerate(real_summary['function_hotspots'][:10]):
            line = (f"│  {i+1:2d}. {hs['function']:<30} "
                    f"calls={hs['calls']:>5}  total={hs['total_ns']/1000:.0f}μs  "
                    f"avg={hs['avg_ns']/1000:.1f}μs")
            lines.append(line[:W-1].ljust(W-1) + "│")
        lines.append("└" + "─" * (W-2) + "┘")
        lines.append("")

    # ─── Virtual Call Counts ───
    if virtual_summary.get('function_call_counts'):
        lines.append("┌" + "─" * (W-2) + "┐")
        lines.append("│  VIRTUAL FUNCTION CALL COUNTS" + " " * (W - 33) + "│")
        lines.append("├" + "─" * (W-2) + "┤")
        sorted_calls = sorted(virtual_summary['function_call_counts'].items(),
                              key=lambda x: -x[1])
        for func, count in sorted_calls[:15]:
            line = f"│  {func:<40} {count:>8} calls"
            lines.append(line[:W-1].ljust(W-1) + "│")
        lines.append("└" + "─" * (W-2) + "┘")
        lines.append("")

    # ─── Latency Analysis ───
    if virtual_summary.get('order_latencies'):
        lats = virtual_summary['order_latencies']
        mc = MetricsCalculator
        lines.append("┌" + "─" * (W-2) + "┐")
        lines.append("│  ORDER LATENCY ANALYSIS (Virtual)" + " " * (W - 37) + "│")
        lines.append("├" + "─" * (W-2) + "┤")
        lines.append(f"│  Orders processed:  {len(lats):,}".ljust(W-1) + "│")
        lines.append(f"│  Avg latency:       {mc.mean(lats):.0f} ns".ljust(W-1) + "│")
        lines.append(f"│  p50 latency:       {mc.percentile(lats, 50):.0f} ns".ljust(W-1) + "│")
        lines.append(f"│  p95 latency:       {mc.percentile(lats, 95):.0f} ns".ljust(W-1) + "│")
        lines.append(f"│  p99 latency:       {mc.percentile(lats, 99):.0f} ns".ljust(W-1) + "│")
        lines.append(f"│  Min latency:       {min(lats):,} ns".ljust(W-1) + "│")
        lines.append(f"│  Max latency:       {max(lats):,} ns".ljust(W-1) + "│")
        lines.append("└" + "─" * (W-2) + "┘")
        lines.append("")

    # ─── Errors ───
    if real_summary.get('error') or virtual_summary.get('error'):
        lines.append("┌" + "─" * (W-2) + "┐")
        lines.append("│  ERRORS" + " " * (W - 11) + "│")
        lines.append("├" + "─" * (W-2) + "┤")
        if real_summary.get('error'):
            lines.append(f"│  Real:    {real_summary['error']}"[:W-1].ljust(W-1) + "│")
        if virtual_summary.get('error'):
            lines.append(f"│  Virtual: {virtual_summary['error']}"[:W-1].ljust(W-1) + "│")
        lines.append("└" + "─" * (W-2) + "┘")
        lines.append("")

    lines.append("=" * W)
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
# PART 5: Main Entry Point
# ═══════════════════════════════════════════════════════════════════════════

def main():
    """Run deep comparison on dummy_submission.py with warmup data."""
    # Paths
    script_dir = Path(__file__).parent.parent
    data_dir = script_dir / "data"
    submission_path = script_dir / "dummy_submission.py"

    if not submission_path.exists():
        print(f"ERROR: {submission_path} not found")
        return

    with open(submission_path, 'r') as f:
        user_code = f.read()

    # Choose scenario - standard has ~10k orders for meaningful comparison
    scenario = "standard"
    orders_path = data_dir / f"{scenario}_orders.csv"

    if not orders_path.exists():
        print(f"ERROR: {orders_path} not found")
        return

    # Prepare input bytes (JSON lines from CSV)
    input_data = bytearray()
    with open(orders_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            input_data.extend((json.dumps(row) + "\n").encode('utf-8'))

    input_bytes = bytes(input_data)
    print(f"Input data prepared: {len(input_bytes):,} bytes from {scenario}_orders.csv")
    print()

    # ─── Phase 1: Real Execution Trace ───
    print("Phase 1: Running REAL execution with deep tracing...")
    print("─" * 60)

    # For real execution, we need a connection that does pure data I/O
    # without any emulation hooks (no cycle counting, no virtual clock).
    real_tracer = RealExecutionTracer()

    import random as _random

    class BareConnection:
        """Pure data I/O connection — no emulation hooks, just bytes in/out."""
        def __init__(self, input_bytes):
            _random.seed(42)  # Match deterministic fragmentation
            self.input_bytes = input_bytes
            self.output_bytes = bytearray()
            self.read_cursor = 0
            self.recv_count = 0
            self.send_count = 0
            self.bytes_received = 0
            self.bytes_sent = 0

        def recv(self, bufsize, flags=0):
            remaining = len(self.input_bytes) - self.read_cursor
            if remaining <= 0:
                return b""
            fragment_size = _random.randint(1, min(bufsize, 50, remaining))
            data = self.input_bytes[self.read_cursor:self.read_cursor + fragment_size]
            self.read_cursor += fragment_size
            self.recv_count += 1
            self.bytes_received += len(data)
            # Track I/O in real tracer
            real_tracer.io_operations.append((time.perf_counter_ns(), 'recv', len(data),
                                              self.bytes_received, self.bytes_sent))
            return data

        def send(self, data, flags=0):
            self.output_bytes.extend(data)
            self.send_count += 1
            self.bytes_sent += len(data)
            real_tracer.io_operations.append((time.perf_counter_ns(), 'send', len(data),
                                              self.bytes_received, self.bytes_sent))
            return len(data)

        def sendall(self, data, flags=0):
            self.output_bytes.extend(data)
            self.send_count += 1
            self.bytes_sent += len(data)
            real_tracer.io_operations.append((time.perf_counter_ns(), 'sendall', len(data),
                                              self.bytes_received, self.bytes_sent))
            return None

        def close(self): pass
        def settimeout(self, value): pass

    bare_conn = BareConnection(input_bytes)

    class BareSocket:
        """Minimal socket mock for real execution tracing."""
        AF_INET = 2
        SOCK_STREAM = 1
        SOL_SOCKET = 65535
        SO_REUSEADDR = 2

        def __init__(self, *args, **kwargs):
            pass
        def bind(self, addr): pass
        def listen(self, backlog=None): pass
        def setsockopt(self, *args): pass
        def close(self): pass
        def accept(self):
            return bare_conn, ("127.0.0.1", 12345)

    # Build the real execution namespace
    real_globals = {
        '__name__': '__main__',
        '__builtins__': __builtins__,
    }

    # Pre-populate sys.modules so the user code's imports work
    import types as types_mod
    fake_socket_mod = types_mod.ModuleType('emu.network')
    fake_socket_mod.Socket = BareSocket
    fake_socket_mod.AF_INET = 2
    fake_socket_mod.SOCK_STREAM = 1
    fake_socket_mod.SOL_SOCKET = 65535
    fake_socket_mod.SO_REUSEADDR = 2

    # Temporarily patch sys.modules
    old_emu_network = sys.modules.get('emu.network')
    sys.modules['emu.network'] = fake_socket_mod

    try:
        real_summary = real_tracer.trace_execution(user_code, real_globals, input_bytes)
    finally:
        if old_emu_network is not None:
            sys.modules['emu.network'] = old_emu_network
        else:
            sys.modules.pop('emu.network', None)

    print(f"  Wall time:        {real_summary['wall_time_ms']:.2f} ms")
    print(f"  CPU time:         {real_summary['cpu_time_ms']:.2f} ms")
    print(f"  Lines executed:   {real_summary['total_lines_executed']:,}")
    print(f"  Bytecode ops:     {real_summary['total_bytecode_ops']:,}")
    print(f"  Peak memory:      {real_summary['peak_memory_mb']:.3f} MB")
    print(f"  Function calls:   {real_summary['total_function_calls']:,}")
    print(f"  Peak call depth:  {real_summary['peak_call_depth']}")
    print(f"  I/O operations:   {real_summary['total_io_calls']:,}")
    print(f"  I/O bytes in:     {real_summary['total_io_bytes_in']:,}")
    print(f"  I/O bytes out:    {real_summary['total_io_bytes_out']:,}")
    print(f"  Total events:     {real_summary['total_events']:,}")
    # Also capture real I/O stats from the bare connection
    real_summary['real_recv_calls'] = bare_conn.recv_count
    real_summary['real_send_calls'] = bare_conn.send_count
    real_summary['real_recv_bytes'] = bare_conn.bytes_received
    real_summary['real_send_bytes'] = bare_conn.bytes_sent
    print(f"  Real recv calls:  {bare_conn.recv_count:,}")
    print(f"  Real send calls:  {bare_conn.send_count:,}")
    print(f"  Real recv bytes:  {bare_conn.bytes_received:,}")
    print(f"  Real send bytes:  {bare_conn.bytes_sent:,}")
    if real_summary.get('error'):
        print(f"  ERROR:            {real_summary['error']}")
    print()

    # ─── Phase 2: Virtual Execution ───
    print("Phase 2: Running VIRTUAL execution through emulation framework...")
    print("─" * 60)

    virtual_tracer = VirtualExecutionTracer()
    virtual_summary = virtual_tracer.run_virtual(user_code, input_bytes)

    print(f"  Wall time:        {virtual_summary['wall_time_ms']:.2f} ms")
    print(f"  Virtual cycles:   {virtual_summary['total_virtual_cycles']:,}")
    print(f"  Virtual time:     {virtual_summary['virtual_time_ms']:.2f} ms")
    print(f"  Peak memory:      {virtual_summary['peak_memory_mb']:.3f} MB")
    print(f"  Function calls:   {virtual_summary['total_function_calls']:,}")
    print(f"  Peak call depth:  {virtual_summary['peak_call_depth']}")
    print(f"  Recv calls:       {virtual_summary.get('recv_calls', 0):,}")
    print(f"  Send calls:       {virtual_summary.get('send_calls', 0):,}")
    print(f"  Increment calls:  {virtual_summary['increment_count']:,}")
    if virtual_summary.get('error'):
        print(f"  ERROR:            {virtual_summary['error']}")
    print()

    # ─── Phase 3: Compare ───
    print("Phase 3: Comparing real vs virtual execution...")
    print("─" * 60)

    comparison = compare_executions(real_summary, virtual_summary)
    report = format_report(comparison, real_summary, virtual_summary)
    print(report)

    # ─── Save detailed data ───
    output_dir = Path(__file__).parent.parent / "analysis"
    output_dir.mkdir(exist_ok=True)

    # Save raw summaries as JSON
    def make_serializable(obj):
        if isinstance(obj, set):
            return list(obj)
        if isinstance(obj, float) and (math.isinf(obj) or math.isnan(obj)):
            return str(obj)
        return obj

    import copy
    real_json = json.loads(json.dumps(real_summary, default=make_serializable))
    virtual_json = json.loads(json.dumps(virtual_summary, default=make_serializable))
    comparison_json = json.loads(json.dumps(comparison, default=make_serializable))

    with open(output_dir / "real_trace.json", 'w') as f:
        json.dump(real_json, f, indent=2, default=str)

    with open(output_dir / "virtual_trace.json", 'w') as f:
        json.dump(virtual_json, f, indent=2, default=str)

    with open(output_dir / "comparison.json", 'w') as f:
        json.dump(comparison_json, f, indent=2, default=str)

    with open(output_dir / "comparison_report.txt", 'w') as f:
        f.write(report)

    print(f"\nDetailed data saved to {output_dir}/")
    print(f"  - real_trace.json")
    print(f"  - virtual_trace.json")
    print(f"  - comparison.json")
    print(f"  - comparison_report.txt")

    # ─── Phase 4: Virtual Event Log Summary ───
    print("\n" + "=" * 80)
    print("  VIRTUAL EXECUTION EVENT LOG SUMMARY")
    print("=" * 80)
    print(f"  Total cycle increments:  {len(virtual_tracer.cycle_log):,}")
    print(f"  Total memory events:     {len(virtual_tracer.mem_log):,}")
    print(f"  Total frame events:      {len(virtual_tracer.frame_log):,}")
    print(f"  Total I/O events:        {len(virtual_tracer.io_log):,}")

    # Cycle increment distribution
    if virtual_tracer.cycle_log:
        amounts = [e[1] for e in virtual_tracer.cycle_log]
        print(f"\n  Cycle increment distribution:")
        print(f"    Min:     {min(amounts):,}")
        print(f"    Max:     {max(amounts):,}")
        print(f"    Mean:    {sum(amounts)/len(amounts):.1f}")
        print(f"    Median:  {sorted(amounts)[len(amounts)//2]:,}")
        print(f"    Total:   {sum(amounts):,}")

        # Histogram of increment sizes
        buckets = defaultdict(int)
        for a in amounts:
            if a <= 5: buckets['1-5'] += 1
            elif a <= 20: buckets['6-20'] += 1
            elif a <= 100: buckets['21-100'] += 1
            elif a <= 500: buckets['101-500'] += 1
            else: buckets['500+'] += 1
        print(f"\n  Cycle increment histogram:")
        for bucket in ['1-5', '6-20', '21-100', '101-500', '500+']:
            count = buckets.get(bucket, 0)
            bar = "█" * min(50, count // max(1, len(amounts) // 50))
            print(f"    {bucket:>8}: {count:>6} {bar}")

    print()


if __name__ == "__main__":
    main()
