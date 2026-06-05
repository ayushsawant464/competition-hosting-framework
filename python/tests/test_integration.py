"""Layer 3 System Tests: Full harness end-to-end (driver-based)."""
import pytest
import os
import tempfile
from pathlib import Path

from harness import BenchmarkHarness, ScoreCard
from emu.environment import BenchmarkEnvironment
from emu.virtual_network import InMemoryConnection


# The data directory is at the root data/
DATA_DIR = Path(__file__).parent.parent.parent / "data"


def _write_submission(code: str) -> str:
    """Driver: write user code to a temp file and return the path."""
    fd, path = tempfile.mkstemp(suffix=".py")
    os.write(fd, code.encode("utf-8"))
    os.close(fd)
    return path


class TestEndToEnd:
    """Black-box: correct output from known submissions."""

    def test_dummy_submission_runs(self):
        """A basic mock submission that echoes trades should produce a ScoreCard."""
        code = '''
import emu.network as network
import json

s = network.Socket(network.AF_INET, network.SOCK_STREAM)
s.setsockopt(network.SOL_SOCKET, network.SO_REUSEADDR, 1)
s.bind(('0.0.0.0', 8080))
s.listen(1)
conn, addr = s.accept()

buffer = ""
while True:
    data = conn.recv(1024)
    if not data:
        break
    buffer += data.decode('utf-8')
    while "\\n" in buffer:
        line, buffer = buffer.split("\\n", 1)
        if line.strip():
            order = json.loads(line)
            trade = {
                "after_sequence": order.get("sequence"),
                "taker_id": order.get("order_id"),
                "maker_id": "MOCK",
                "price": order.get("price", "100"),
                "quantity": order.get("quantity", "10"),
                "taker_side": order.get("side", "BUY")
            }
            conn.sendall((json.dumps(trade) + "\\n").encode('utf-8'))
conn.close()
'''
        path = _write_submission(code)
        try:
            harness = BenchmarkHarness(user_code_path=path, scenario_dir=str(DATA_DIR))
            score = harness.score_scenario("warmup")
            assert isinstance(score, ScoreCard)
            assert score.virtual_cycles > 0
            assert score.peak_memory_mb >= 0
            assert score.composite_score >= 0
            assert score.total_trades_actual > 0
            # Latency should be measured
            assert score.avg_latency_ns > 0
            assert score.p95_latency_ns > 0
            assert score.p99_latency_ns > 0
            # Memory profile
            assert score.memory_p50_mb >= 0
            # Throughput
            assert score.throughput_ops > 0
            # I/O
            assert score.io_recv_calls > 0
            assert score.io_bytes_per_recv > 0
        finally:
            os.unlink(path)

    def test_empty_submission_zero_correctness(self):
        """A submission that produces no output → correctness ≈ 0."""
        code = '''
import emu.network as network

s = network.Socket(network.AF_INET, network.SOCK_STREAM)
s.bind(('0.0.0.0', 8080))
s.listen(1)
conn, addr = s.accept()
# Read all data but produce no output
while True:
    data = conn.recv(1024)
    if not data:
        break
conn.close()
'''
        path = _write_submission(code)
        try:
            harness = BenchmarkHarness(user_code_path=path, scenario_dir=str(DATA_DIR))
            score = harness.score_scenario("warmup")
            assert score.correctness_score == 0.0
            assert score.total_trades_actual == 0
        finally:
            os.unlink(path)


class TestSandboxSecurity:
    """Black-box: blocked imports raise correct errors."""

    def test_import_os_blocked(self):
        env = BenchmarkEnvironment()
        result = env.run("import os")
        assert result.error is not None
        assert isinstance(result.error, ImportError)

    def test_import_socket_blocked(self):
        env = BenchmarkEnvironment()
        result = env.run("import socket")
        assert result.error is not None
        assert "emu.network" in str(result.error)

    def test_import_subprocess_blocked(self):
        env = BenchmarkEnvironment()
        result = env.run("import subprocess")
        assert result.error is not None

    def test_import_json_allowed(self):
        env = BenchmarkEnvironment()
        result = env.run("import json")
        assert result.error is None

    def test_import_struct_allowed(self):
        env = BenchmarkEnvironment()
        result = env.run("import struct")
        assert result.error is None


class TestClockIntegration:
    """White-box: verify clock advances during execution."""

    def test_clock_advances(self):
        env = BenchmarkEnvironment()
        result = env.run("for i in range(100): pass")
        assert result.error is None
        assert result.clock.current_ns > 0

    def test_memory_timeline_populated(self):
        """Memory timeline should have entries after a large-ish run."""
        env = BenchmarkEnvironment()
        # Run enough cycles to trigger multiple memory samples (every 10k cycles)
        result = env.run("x = [0]*1000\nfor i in range(50000): x.append(i)")
        assert result.error is None
        assert len(result.memory_tracker.timeline) > 0
