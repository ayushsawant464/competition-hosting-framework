"""
Emulation environment exports.

This module now serves as a clean facade, maintaining backward compatibility
while adhering to SOLID principles by delegating to specialized modules.
"""

from emu.state import EmulationState
from emu.sandbox import BenchmarkEnvironment, ExecutionResult

__all__ = [
    'EmulationState',
    'BenchmarkEnvironment',
    'ExecutionResult'
]
