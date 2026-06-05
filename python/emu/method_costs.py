"""
Method cost modeling for the emulation layer.

Separates the complexity of calculating algorithmic costs for method calls
(e.g., list.sort, pandas.groupby) from the core state management.
"""

import math
from typing import Any

def estimate_method_cost(obj: Any, method_name: str, args: tuple, kwargs: dict) -> int:
    """
    Estimate the virtual cycle cost of calling a method on an object.
    
    Args:
        obj: The object the method is being called on
        method_name: The name of the method
        args: Positional arguments to the method
        kwargs: Keyword arguments to the method
        
    Returns:
        int: Estimated cycle cost (before scaling)
    """
    cycles = 5  # Default fallback cost
    is_pd_or_pl = False
    
    try:
        tp_str = str(type(obj))
        if 'pandas' in tp_str or 'polars' in tp_str:
            is_pd_or_pl = True
        elif hasattr(obj, '__name__') and obj.__name__ in ('pandas', 'polars'):
            is_pd_or_pl = True

        if is_pd_or_pl:
            n = 1
            if hasattr(obj, 'shape') and isinstance(obj.shape, tuple) and len(obj.shape) >= 1:
                n = obj.shape[0]
                if len(obj.shape) > 1:
                    n *= obj.shape[1]
            elif hasattr(obj, '__len__'):
                n = len(obj)

            if method_name in ('read_csv', 'read_parquet', 'DataFrame', 'Series'):
                cycles = 5
            elif method_name in ('groupby', 'sort_values', 'sort', 'merge', 'join', 'pivot'):
                cycles = int(n * math.log2(max(n, 2)) * 12)
            elif method_name in ('sum', 'mean', 'std', 'min', 'max', 'filter', 'select', 'dropna', 'fillna', 'apply'):
                cycles = n * 4
            else:
                cycles = n * 2 + 5
        else:
            n = len(obj) if hasattr(obj, '__len__') else 1
            if isinstance(obj, str):
                if method_name in ('split', 'find', 'upper', 'lower', 'encode'):
                    cycles = n * 2
                elif method_name == 'join':
                    cycles = sum(len(s) for s in args[0]) * 2 if args and hasattr(args[0], '__iter__') else n * 2
                elif method_name == 'replace':
                    cycles = n * 4
            elif isinstance(obj, list):
                if method_name in ('append', 'pop'):
                    cycles = 5
                elif method_name == 'extend':
                    cycles = (len(args[0]) if args and hasattr(args[0], '__len__') else 1) * 3
                elif method_name in ('index', 'count', 'remove'):
                    cycles = n * 3
                elif method_name == 'sort':
                    cycles = int(n * math.log2(max(n, 2)) * 8)
            elif isinstance(obj, dict):
                if method_name in ('get', 'pop'):
                    cycles = 5
                elif method_name == 'update':
                    cycles = (len(args[0]) if args and hasattr(args[0], '__len__') else 1) * 5
            elif isinstance(obj, set):
                if method_name in ('add', 'remove', 'discard', 'pop'):
                    cycles = 5
                elif method_name in ('union', 'intersection', 'difference'):
                    other_len = (len(args[0]) if args and hasattr(args[0], '__len__') else 1)
                    cycles = (n + other_len) * 3
    except Exception:
        pass
        
    return cycles

def is_data_science_method(obj: Any) -> bool:
    """Check if the object is a pandas or polars object."""
    try:
        tp_str = str(type(obj))
        if 'pandas' in tp_str or 'polars' in tp_str:
            return True
        elif hasattr(obj, '__name__') and obj.__name__ in ('pandas', 'polars'):
            return True
    except Exception:
        pass
    return False
