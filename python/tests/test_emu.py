import pytest
from emu.environment import BenchmarkEnvironment

def test_ast_instrumentation():
    """Test that our AST rewriter or tracer correctly counts operations."""
    code = """
def compute():
    x = 0
    for i in range(10):
        x += i
    return x
compute()
"""
    env = BenchmarkEnvironment()
    result = env.run(code)
    
    assert result.error is None
    assert result.virtual_cycles > 0

def test_sandbox_security():
    """Test that the sandbox prevents malicious code."""
    malicious_code = """
import os
"""
    env = BenchmarkEnvironment()
    result = env.run(malicious_code)
    assert result.error is not None
    assert isinstance(result.error, ImportError)

def test_memory_limits():
    """Test that allocating too much memory raises a MemoryError."""
    alloc_code = """
x = [0] * 10_000_000
"""
    env = BenchmarkEnvironment(memory_limit_mb=1)
    result = env.run(alloc_code)
    assert result.error is not None
    assert isinstance(result.error, MemoryError)

def test_recursion_detection():
    """Test that recursion limits are enforced and raise RecursionError."""
    recursion_code = """
def recurse(n):
    if n <= 0:
        return 0
    return recurse(n - 1)
recurse(100)
"""
    env = BenchmarkEnvironment()
    # Set a low max stack depth for testing
    env.state.frame_tracker.max_depth = 50
    result = env.run(recursion_code)
    assert result.error is not None
    assert isinstance(result.error, RecursionError)
    assert "Virtual stack overflow" in str(result.error)

def test_cost_aware_builtins():
    """Test that size-aware builtins charge cycles proportional to size."""
    small_sort = """
l = list(range(10))
sorted(l)
"""
    large_sort = """
l = list(range(100))
sorted(l)
"""
    env = BenchmarkEnvironment()
    res1 = env.run(small_sort)
    assert res1.error is None
    
    env2 = BenchmarkEnvironment()
    res2 = env2.run(large_sort)
    assert res2.error is None
    
    # Large sort must consume significantly more cycles than small sort
    assert res2.virtual_cycles > res1.virtual_cycles
