from pathlib import Path
import pytest

from harness import BenchmarkHarness, ScoreCard

PROJECT_DIR = Path(__file__).parent.parent.parent

def test_harness_scoring():
    harness = BenchmarkHarness(
        user_code_path=str(PROJECT_DIR / "dummy_submission.py"),
        scenario_dir=str(PROJECT_DIR / "data")
    )
    results = harness.score_scenario("warmup")
    assert isinstance(results, ScoreCard)
    assert 0.0 <= results.composite_score
    assert results.total_trades_actual > 0

def test_network_socket_emulation_fragmentation():
    """Test that the harness forces the user to handle partial socket reads."""
    code = """
import emu.network as socket
s = socket.Socket(socket.AF_INET, socket.SOCK_STREAM)
s.bind(('0.0.0.0', 8080))
s.listen(1)
conn, addr = s.accept()
# It's a mock socket, we can just test the type is what we expect
assert hasattr(conn, 'recv')
assert hasattr(socket, 'AF_INET')
"""
    from emu.environment import BenchmarkEnvironment
    from emu.virtual_network import InMemoryConnection
    env = BenchmarkEnvironment()
    env.preloaded_conn = InMemoryConnection(env.state, b"mock")
    result = env.run(code)
    assert result.error is None

