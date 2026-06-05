"""
Sandbox execution environment.

Follows the Single Responsibility Principle by isolating the secure code
execution and namespace configuration from state tracking.
"""

import builtins
import sys
import types
from typing import Dict, Any, Optional

from emu.state import EmulationState
from emu.ast_transformer import instrument_code
from emu.cost_models import wrap_builtins
from emu.gc import create_gc_module

class ExecutionResult:
    def __init__(self, state: EmulationState):
        self.virtual_cycles = state.virtual_cycles
        self.peak_memory_mb = state.memory.get_peak_mb()
        self.clock = state.clock
        self.memory_tracker = state.memory
        self.frame_tracker = state.frame_tracker
        self.error: Optional[Exception] = None


def create_sandbox_globals(state: EmulationState) -> Dict[str, Any]:
    """
    Create a restricted globals dictionary.
    """
    safe_builtins = {
        'abs': builtins.abs,
        'all': builtins.all,
        'any': builtins.any,
        'bool': builtins.bool,
        'bytes': builtins.bytes,
        'chr': builtins.chr,
        'dict': builtins.dict,
        'divmod': builtins.divmod,
        'enumerate': builtins.enumerate,
        'filter': builtins.filter,
        'float': builtins.float,
        'format': builtins.format,
        'frozenset': builtins.frozenset,
        'getattr': builtins.getattr,
        'hasattr': builtins.hasattr,
        'hash': builtins.hash,
        'hex': builtins.hex,
        'id': builtins.id,
        'int': builtins.int,
        'isinstance': builtins.isinstance,
        'issubclass': builtins.issubclass,
        'iter': builtins.iter,
        'len': builtins.len,
        'list': builtins.list,
        'map': builtins.map,
        'max': builtins.max,
        'min': builtins.min,
        'next': builtins.next,
        'object': builtins.object,
        'oct': builtins.oct,
        'open': state.vfs.open,  # Inject Virtual File System
        'ord': builtins.ord,
        'pow': builtins.pow,
        'print': builtins.print,
        'range': builtins.range,
        'repr': builtins.repr,
        'reversed': builtins.reversed,
        'round': builtins.round,
        'set': builtins.set,
        'setattr': builtins.setattr,
        'slice': builtins.slice,
        'sorted': builtins.sorted,
        'str': builtins.str,
        'sum': builtins.sum,
        'tuple': builtins.tuple,
        'type': builtins.type,
        'zip': builtins.zip,
        'Exception': builtins.Exception,
        'ValueError': builtins.ValueError,
        'TypeError': builtins.TypeError,
        'KeyError': builtins.KeyError,
        'IndexError': builtins.IndexError,
    }

    cost_aware_builtins = wrap_builtins(safe_builtins, state)
    
    # Create the virtual gc module for the user
    gc_mod, _ = create_gc_module(state)

    def _safe_import(name: str, globals=None, locals=None, fromlist=(), level=0):
        # Whitelist safe standard libraries
        # Adding 'struct' and 'io' to allow competitors to build custom binary parsers (e.g. FIX/SBE)
        allowed = {'math', 'json', 'collections', 'itertools', 'datetime', 'struct', 'io'}
        # We allow pandas and polars to be imported for data science
        # But we must be careful not to allow them to do arbitrary OS access
        if name in ('pandas', 'polars'):
            return __import__(name, globals, locals, fromlist, level)

        if name in allowed:
            return __import__(name, globals, locals, fromlist, level)
            
        if name == 'emu.network' or name == 'network':
            return __import__(name, globals, locals, fromlist, level)
            
        if name == 'gc':
            return gc_mod

        raise ImportError(f"Importing '{name}' is disabled in the sandbox.")

    cost_aware_builtins['__import__'] = _safe_import

    # Inject __emu__ into sys.modules so emu.network can find the state
    import sys
    sys.modules['__emu__'] = state

    return {
        '__builtins__': cost_aware_builtins,
        '__emu__': state,
    }


class BenchmarkEnvironment:
    """
    Sandboxed environment that executes instrumented Python code.
    """
    def __init__(self, memory_limit_mb: int = 512, preloaded_conn=None):
        self.state = EmulationState(memory_limit_mb)
        self.state.preloaded_conn = preloaded_conn
        self.memory_limit_mb = memory_limit_mb

    def run(self, source_code: str) -> ExecutionResult:
        """
        Instrument and execute the source code.
        """
        result = ExecutionResult(self.state)
        try:
            # 1. Parse and instrument the AST
            instrumented = instrument_code(source_code)
            # 2. Compile to bytecode
            compiled = compile(instrumented, '<benchmark>', 'exec')
            # 3. Create sandbox globals
            sandbox_globals = create_sandbox_globals(self.state)
            
            # Start tracking
            self.state.memory.start()
            
            # 4. Execute
            exec(compiled, sandbox_globals)

            result.peak_memory_mb = self.state.memory.get_peak_mb()
            result.virtual_cycles = self.state.virtual_cycles
            result.frame_tracker = self.state.frame_tracker
            result.memory_tracker = self.state.memory
            result.clock = self.state.clock
            
        except Exception as e:
            result.error = e
        finally:
            self.state.memory.stop()

        return result
