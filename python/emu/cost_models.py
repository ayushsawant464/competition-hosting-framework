"""Cost-aware proxies for Python builtins and standard-library modules.

This module is the bridge between the AST-level instrumentation (which adds
a flat per-statement cost) and *algorithmic* complexity awareness.  Every
wrapped builtin or module function is fronted by a :class:`CostProxy` that
computes a virtual-cycle cost **proportional to the size of its input**
before delegating to the real implementation.

Design goals
~~~~~~~~~~~~
* **Standalone** – no imports from the ``emu`` package so it can be tested
  and reasoned about in isolation.
* **Fail-safe** – if a cost function raises (e.g. the argument has no
  ``len``), a small fallback cost is charged rather than crashing the
  sandbox.
* **Composable** – :func:`wrap_builtins` patches a ``safe_builtins`` dict
  in-place; :func:`wrap_module` returns a :class:`CostWrappedModule` that
  can replace a real module in ``sys.modules``.
"""

from __future__ import annotations

import math
import sys
from types import ModuleType
from typing import Any, Callable, Dict, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_FALLBACK_LEN: int = 8  # Charged when we cannot determine the size.
_FALLBACK_COST: int = 10  # Charged when a cost function itself raises.


def _safe_len(obj: Any, default: int = _FALLBACK_LEN) -> int:
    """Return ``len(obj)`` if possible, else *default*.

    Handles iterators, generators, and objects without ``__len__`` gracefully.
    """
    try:
        return len(obj)  # type: ignore[arg-type]
    except TypeError:
        return default


def _first_arg(args: Tuple[Any, ...], default: Any = None) -> Any:
    """Safely return the first positional argument."""
    return args[0] if args else default


def _estimate_json_size(obj: Any, *, _depth: int = 0, _limit: int = 32) -> int:
    """Recursively estimate the character-count of a JSON serialisation.

    The estimate is *deliberately* cheap and approximate – we cap recursion
    at *_limit* levels and fall back to ``len(str(obj))`` for unknown types.
    """
    if _depth > _limit:
        return 1

    if obj is None:
        return 4  # "null"
    if isinstance(obj, bool):
        return 5  # "false"
    if isinstance(obj, (int, float)):
        return len(str(obj))
    if isinstance(obj, str):
        return len(obj) + 2  # quotes
    if isinstance(obj, (list, tuple)):
        total = 2  # brackets
        for item in obj:
            total += _estimate_json_size(item, _depth=_depth + 1, _limit=_limit) + 1
        return total
    if isinstance(obj, dict):
        total = 2  # braces
        for k, v in obj.items():
            total += (
                _estimate_json_size(k, _depth=_depth + 1, _limit=_limit)
                + _estimate_json_size(v, _depth=_depth + 1, _limit=_limit)
                + 2  # colon + comma
            )
        return total
    # Fallback for arbitrary objects.
    try:
        return len(str(obj))
    except Exception:
        return _FALLBACK_LEN


# ---------------------------------------------------------------------------
# CostProxy
# ---------------------------------------------------------------------------

class CostProxy:
    """Wraps a callable, charging virtual CPU cycles before each call.

    Parameters
    ----------
    real_fn:
        The actual builtin or library function to delegate to.
    cost_fn:
        ``(args, kwargs) -> int`` – returns the number of virtual cycles to
        charge for a particular invocation.
    emu_state:
        The :class:`EmulationState` instance whose ``increment`` method is
        called to record the cycles.
    """
    def __init__(
        self,
        real_fn: Callable[..., Any],
        cost_fn: Callable[[Tuple[Any, ...], Dict[str, Any]], int],
        emu_state: Any,
    ) -> None:
        self._real_fn = real_fn
        self._cost_fn = cost_fn
        self._emu_state = emu_state

        # Preserve identity of the wrapped function.
        self.__name__: str = getattr(real_fn, "__name__", repr(real_fn))
        self.__doc__: Optional[str] = getattr(real_fn, "__doc__", None)

    # ------------------------------------------------------------------
    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        # 1. Compute cost (fail-safe).
        try:
            cycles: int = int(self._cost_fn(args, kwargs))
        except Exception:
            cycles = _FALLBACK_COST

        # Scale down C built-in cycles to reflect native execution speed relative to Python interpreter
        cycles = max(1, cycles // 150)

        # 2. Charge the cycles.
        self._emu_state.increment(cycles)

        # 3. Delegate to the real function.
        result = self._real_fn(*args, **kwargs)

        # 4. Track the output allocation anonymously.
        try:
            from emu.virtual_memory import estimate_object_size
            size = estimate_object_size(result)
            if hasattr(self._emu_state, 'track_anonymous'):
                self._emu_state.track_anonymous(size)
            elif hasattr(self._emu_state, 'memory') and hasattr(self._emu_state.memory, 'track_anonymous'):
                self._emu_state.memory.track_anonymous(size)
        except Exception:
            pass

        return result

    # ------------------------------------------------------------------
    def __repr__(self) -> str:
        return f"<CostProxy {self.__name__}>"


# ---------------------------------------------------------------------------
# CostWrappedModule
# ---------------------------------------------------------------------------

class CostWrappedModule(ModuleType):
    """Proxy around a real module that interposes cost-aware wrappers.

    Any attribute listed in *cost_map* is returned as a :class:`CostProxy`;
    all other attributes are fetched from the underlying *real_module*
    unchanged.

    Parameters
    ----------
    real_module:
        The actual imported module object.
    cost_map:
        ``{'function_name': cost_fn, ...}`` – names to wrap and their
        corresponding cost functions.
    emu_state:
        The shared :class:`EmulationState`.
    """

    def __init__(
        self,
        real_module: ModuleType,
        cost_map: Dict[str, Callable[[Tuple[Any, ...], Dict[str, Any]], int]],
        emu_state: Any,
    ) -> None:
        super().__init__(
            name=getattr(real_module, "__name__", "wrapped"),
            doc=getattr(real_module, "__doc__", None),
        )
        self._real_module = real_module
        self._cost_map = cost_map
        self._emu_state = emu_state

        # Cache proxies so we build each one only once.
        self._proxy_cache: Dict[str, CostProxy] = {}

    # ------------------------------------------------------------------
    def __getattr__(self, name: str) -> Any:
        if name in self._cost_map:
            if name not in self._proxy_cache:
                real_fn = getattr(self._real_module, name)
                self._proxy_cache[name] = CostProxy(
                    real_fn, self._cost_map[name], self._emu_state
                )
            return self._proxy_cache[name]
        return getattr(self._real_module, name)

    def __repr__(self) -> str:
        return f"<CostWrappedModule {self._real_module.__name__!r}>"


# =========================================================================
# Built-in cost functions
# =========================================================================
# Every function below has the signature ``(args, kwargs) -> int``.
# =========================================================================

# -- Sorting (TimSort = O(n log n)) ----------------------------------------

def _cost_sorted(args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> int:
    n = _safe_len(_first_arg(args, []))
    if n <= 1:
        return 3
    return int(n * math.log2(n) * 8)


# -- Aggregation (O(n)) ----------------------------------------------------

def _cost_linear_3(args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> int:
    """sum / min / max / any / all – O(n) with 3 cycles per element."""
    return _safe_len(_first_arg(args, [])) * 3


def _cost_len(args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> int:
    return 3  # O(1) for built-in containers


# -- Constructors (O(n)) ---------------------------------------------------

def _cost_list(args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> int:
    if not args:
        return 3
    return _safe_len(args[0]) * 3


def _cost_dict(args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> int:
    if not args:
        return 3
    return _safe_len(args[0]) * 5


def _cost_set(args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> int:
    if not args:
        return 3
    return _safe_len(args[0]) * 4


def _cost_tuple(args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> int:
    if not args:
        return 3
    return _safe_len(args[0]) * 2


def _cost_frozenset(args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> int:
    if not args:
        return 3
    return _safe_len(args[0]) * 4


# -- Type conversions -------------------------------------------------------

def _cost_int(args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> int:
    if not args:
        return 5
    try:
        return 5 + len(str(args[0]))
    except Exception:
        return 5


def _cost_float(args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> int:
    return 5


def _cost_str(args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> int:
    if not args:
        return 10
    try:
        return 10 + len(str(args[0])) * 2
    except Exception:
        return 10


def _cost_bytes(args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> int:
    return 10


def _cost_bool(args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> int:
    return 3


# -- I/O --------------------------------------------------------------------

def _cost_print(args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> int:
    total = 50
    for arg in args:
        try:
            total += len(str(arg)) * 2
        except Exception:
            total += _FALLBACK_LEN * 2
    return total


# -- Hashing / identity (O(1)) ---------------------------------------------

_cost_hash = lambda a, kw: 5
_cost_id = lambda a, kw: 1
_cost_abs = lambda a, kw: 3
_cost_chr = lambda a, kw: 3
_cost_ord = lambda a, kw: 3
_cost_hex = lambda a, kw: 3
_cost_oct = lambda a, kw: 3
_cost_round = lambda a, kw: 5
_cost_pow = lambda a, kw: 5
_cost_divmod = lambda a, kw: 5


# -- Iteration helpers (O(1) creation) -------------------------------------

_cost_range = lambda a, kw: 5
_cost_enumerate = lambda a, kw: 5
_cost_zip = lambda a, kw: 5
_cost_map = lambda a, kw: 5
_cost_filter = lambda a, kw: 5
_cost_reversed = lambda a, kw: 5
_cost_iter = lambda a, kw: 5
_cost_next = lambda a, kw: 5


# -- Attribute access (O(1) + MRO) -----------------------------------------

_cost_getattr = lambda a, kw: 8
_cost_hasattr = lambda a, kw: 8
_cost_setattr = lambda a, kw: 8
_cost_isinstance = lambda a, kw: 5
_cost_issubclass = lambda a, kw: 5
_cost_callable = lambda a, kw: 3
_cost_type = lambda a, kw: 3


# -- String formatting ------------------------------------------------------

def _cost_format(args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> int:
    if not args:
        return 10
    try:
        return 10 + len(str(args[0])) * 2
    except Exception:
        return 10


def _cost_repr(args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> int:
    if not args:
        return 10
    try:
        return 10 + len(str(args[0])) * 2
    except Exception:
        return 10


# -- Misc -------------------------------------------------------------------

_cost_object = lambda a, kw: 3
_cost_slice = lambda a, kw: 3


# =========================================================================
# BUILTIN_COSTS registry
# =========================================================================

BUILTIN_COSTS: Dict[str, Callable[[Tuple[Any, ...], Dict[str, Any]], int]] = {
    # Sorting
    "sorted": _cost_sorted,

    # Aggregation
    "sum": _cost_linear_3,
    "min": _cost_linear_3,
    "max": _cost_linear_3,
    "any": _cost_linear_3,
    "all": _cost_linear_3,
    "len": _cost_len,

    # Constructors
    "list": _cost_list,
    "dict": _cost_dict,
    "set": _cost_set,
    "tuple": _cost_tuple,
    "frozenset": _cost_frozenset,

    # Type conversions
    "int": _cost_int,
    "float": _cost_float,
    "str": _cost_str,
    "bytes": _cost_bytes,
    "bool": _cost_bool,

    # I/O
    "print": _cost_print,

    # Hashing / identity
    "hash": _cost_hash,
    "id": _cost_id,
    "abs": _cost_abs,
    "chr": _cost_chr,
    "ord": _cost_ord,
    "hex": _cost_hex,
    "oct": _cost_oct,
    "round": _cost_round,
    "pow": _cost_pow,
    "divmod": _cost_divmod,

    # Iteration helpers
    "range": _cost_range,
    "enumerate": _cost_enumerate,
    "zip": _cost_zip,
    "map": _cost_map,
    "filter": _cost_filter,
    "reversed": _cost_reversed,
    "iter": _cost_iter,
    "next": _cost_next,

    # Attribute access
    "getattr": _cost_getattr,
    "hasattr": _cost_hasattr,
    "setattr": _cost_setattr,
    "isinstance": _cost_isinstance,
    "issubclass": _cost_issubclass,
    "callable": _cost_callable,
    "type": _cost_type,

    # String formatting
    "format": _cost_format,
    "repr": _cost_repr,

    # Misc
    "object": _cost_object,
    "slice": _cost_slice,
}


# =========================================================================
# Module-level cost maps (for CostWrappedModule)
# =========================================================================

JSON_COSTS: Dict[str, Callable[[Tuple[Any, ...], Dict[str, Any]], int]] = {
    "loads": lambda args, kw: (
        len(args[0]) * 15
        if args and isinstance(args[0], (str, bytes))
        else 50
    ),
    "dumps": lambda args, kw: (
        _estimate_json_size(args[0]) * 12 if args else 50
    ),
    "load": lambda args, kw: 500,  # File-backed; fixed estimate.
    "dump": lambda args, kw: (
        _estimate_json_size(args[0]) * 12 if args else 50
    ),
}


# =========================================================================
# Public API – wrapping helpers
# =========================================================================

def wrap_builtins(safe_builtins: Dict[str, Any], emu_state: Any) -> Dict[str, Any]:
    """Replace entries in *safe_builtins* with :class:`CostProxy` wrappers.

    Only builtins that appear in :data:`BUILTIN_COSTS` are wrapped; all
    others are left untouched.  The dict is modified **in-place** and also
    returned for convenience.

    Parameters
    ----------
    safe_builtins:
        The ``{'name': builtin_fn, ...}`` dict normally passed as
        ``__builtins__`` in the sandbox globals.
    emu_state:
        The shared :class:`EmulationState` that exposes ``.increment()``.

    Returns
    -------
    dict
        The same *safe_builtins* dict, with cost-proxied entries.
    """
    for name, cost_fn in BUILTIN_COSTS.items():
        if name in safe_builtins:
            real_fn = safe_builtins[name]
            # Avoid double-wrapping.
            if not isinstance(real_fn, CostProxy):
                safe_builtins[name] = CostProxy(real_fn, cost_fn, emu_state)
    return safe_builtins


def wrap_module(
    module: ModuleType,
    cost_map: Dict[str, Callable[[Tuple[Any, ...], Dict[str, Any]], int]],
    emu_state: Any,
) -> CostWrappedModule:
    """Return a :class:`CostWrappedModule` that proxies *module*.

    Parameters
    ----------
    module:
        The real module object (e.g. ``json``).
    cost_map:
        ``{'function_name': cost_fn, ...}`` – only the named functions are
        wrapped; every other attribute is delegated unchanged.
    emu_state:
        The shared :class:`EmulationState`.

    Returns
    -------
    CostWrappedModule
        A drop-in replacement for *module*.
    """
    return CostWrappedModule(module, cost_map, emu_state)
