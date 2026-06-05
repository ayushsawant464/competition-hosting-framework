import csv
import json
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, List, Optional

from emu.environment import BenchmarkEnvironment
from emu.virtual_network import InMemoryConnection
from emu.metrics import MetricsCalculator


@dataclass
class ScoreCard:
    # 1. Correctness
    correctness_score: float
    total_trades_expected: int
    total_trades_actual: int

    # 2. Memory Profile
    memory_p50_mb: float
    memory_p95_mb: float
    memory_p99_mb: float
    memory_volatility: float
    memory_curve_spike: float

    # 3. Latency (nanoseconds)
    avg_latency_ns: float
    p95_latency_ns: float
    p99_latency_ns: float

    # 4. Throughput
    throughput_ops: float       # Orders per virtual second
    throughput_degradation: float  # Ratio last 10% / first 10%

    # 5. I/O Efficiency
    io_recv_calls: int
    io_bytes_per_recv: float

    # Overall
    virtual_cycles: int
    peak_memory_mb: float
    composite_score: float


class BenchmarkHarness:
    def __init__(self, user_code_path: str, scenario_dir: str = "data"):
        self.user_code_path = user_code_path
        self.scenario_dir = Path(scenario_dir)

    def score_scenario(self, scenario_name: str) -> ScoreCard:
        with open(self.user_code_path, 'r') as f:
            code = f.read()

        orders_path = self.scenario_dir / f"{scenario_name}_orders.csv"
        expected_path = self.scenario_dir / f"{scenario_name}_expected.csv"

        # 1. Preload the CSV data into bytes (JSON lines)
        input_data = bytearray()
        with open(orders_path, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                input_data.extend((json.dumps(row) + "\n").encode('utf-8'))

        # 2. Setup the environment and mocked connection
        env = BenchmarkEnvironment(memory_limit_mb=512)
        mock_conn = InMemoryConnection(
            env.state,
            input_bytes=bytes(input_data),
            clock=env.state.clock
        )
        env.preloaded_conn = mock_conn
        env.state.preloaded_conn = mock_conn

        # 3. Execute the code
        result = env.run(code)

        # 4. Parse output trades
        output_bytes = mock_conn.output_bytes
        actual_trades = []
        buffer = output_bytes.decode('utf-8')
        lines = buffer.strip().split('\n')
        for line in lines:
            if line.strip():
                try:
                    actual_trades.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

        # 5. Load expected trades
        expected_trades = []
        with open(expected_path, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                expected_trades.append(row)

        # --- Compute 5 Macro Scores ---
        mc = MetricsCalculator

        # 1. Correctness
        correctness = 0.0
        if not expected_trades:
            correctness = 1.0 if not actual_trades else 0.0
        else:
            # We want to match trades based on taker_id, maker_id, price, and quantity.
            # Convert trades to a hashable format to count matches.
            def trade_to_tuple(t):
                return (t.get('taker_id'), t.get('maker_id'), str(float(t.get('price', 0))), str(float(t.get('quantity', 0))))
            
            expected_counts = {}
            for t in expected_trades:
                k = trade_to_tuple(t)
                expected_counts[k] = expected_counts.get(k, 0) + 1
            
            matches = 0
            for t in actual_trades:
                k = trade_to_tuple(t)
                if expected_counts.get(k, 0) > 0:
                    matches += 1
                    expected_counts[k] -= 1
                    
            correctness = matches / len(expected_trades)

        # 2. Memory Profile
        mem_profile = result.memory_tracker.get_memory_profile()

        # 3. Latency
        latencies = mock_conn.order_latencies
        avg_latency = mc.mean(latencies)
        p95_latency = mc.percentile(latencies, 95)
        p99_latency = mc.percentile(latencies, 99)

        # 4. Throughput
        total_orders = len(actual_trades)
        total_time_ns = result.clock.current_ns
        total_time_sec = total_time_ns / 1_000_000_000 if total_time_ns > 0 else 1
        throughput = total_orders / total_time_sec if total_time_sec > 0 else 0
        degradation = mc.degradation_ratio(latencies) if len(latencies) >= 2 else 1.0

        # 5. I/O Efficiency
        io_recv_calls = mock_conn.recv_count
        io_bytes_per_recv = (mock_conn.bytes_received / io_recv_calls) if io_recv_calls > 0 else 0

        # --- Composite Score ---
        # Normalize each sub-score to [0, 1]
        # For latency/memory/throughput, lower is better → invert
        correctness_subscore = correctness  # Already [0, 1]

        # --- Memory Noise Filtering ---
        # Physical memory tracked via tracemalloc is subject to GC timing and host alignment noise.
        # If memory footprint is extremely low (< 1.0 MB), it represents interpreter overhead.
        # We cap it at exactly 0.20 MB to ensure perfect score determinism, and round higher values to 0.1 MB.
        p50_mem = mem_profile.get('p50_mb', 0)
        p95_mem = mem_profile.get('p95_mb', 0)
        p99_mem = mem_profile.get('p99_mb', 0)
        peak_mem = result.peak_memory_mb

        if p50_mem < 1.0: p50_mem = 0.20
        else: p50_mem = round(p50_mem * 10) / 10.0

        if p95_mem < 1.0: p95_mem = 0.20
        else: p95_mem = round(p95_mem * 10) / 10.0

        if p99_mem < 1.0: p99_mem = 0.20
        else: p99_mem = round(p99_mem * 10) / 10.0

        if peak_mem < 1.0: peak_mem = 0.20
        else: peak_mem = round(peak_mem * 10) / 10.0

        memory_volatility = 0.0 if p99_mem <= 0.20 else mem_profile.get('volatility', 0)
        memory_spike = 0.0 if p99_mem <= 0.20 else mem_profile.get('max_spike', 0)

        # --- Composite Score ---
        # Normalize each sub-score to [0, 1]
        # For latency/memory/throughput, lower is better → invert
        correctness_subscore = correctness  # Already [0, 1]

        # Memory: lower p99 is better. Tightened baseline = 5.0 MB (instead of 100.0)
        mem_subscore = max(0, 1.0 - (p99_mem / 5.0))

        # Latency: lower p99 is better. Tightened baseline = 2,000 ns (2.0 μs)
        lat_subscore = max(0, 1.0 - (p99_latency / 2000.0)) if p99_latency > 0 else 1.0

        # Throughput: higher is better. Tightened baseline = 250,000 ops/sec
        tp_subscore = min(1.0, throughput / 250000.0)

        # I/O: higher bytes_per_recv is better. Tightened baseline = 128 bytes
        io_subscore = min(1.0, io_bytes_per_recv / 128.0)

        composite = (
            correctness_subscore * 0.30 +
            mem_subscore * 0.15 +
            lat_subscore * 0.25 +
            tp_subscore * 0.20 +
            io_subscore * 0.10
        ) * 10000

        cycles = result.virtual_cycles
        if cycles == 0:
            cycles = 1

        return ScoreCard(
            correctness_score=correctness,
            total_trades_expected=len(expected_trades),
            total_trades_actual=len(actual_trades),
            memory_p50_mb=p50_mem,
            memory_p95_mb=p95_mem,
            memory_p99_mb=p99_mem,
            memory_volatility=memory_volatility,
            memory_curve_spike=memory_spike,
            avg_latency_ns=avg_latency,
            p95_latency_ns=p95_latency,
            p99_latency_ns=p99_latency,
            throughput_ops=throughput,
            throughput_degradation=degradation,
            io_recv_calls=io_recv_calls,
            io_bytes_per_recv=io_bytes_per_recv,
            virtual_cycles=cycles,
            peak_memory_mb=peak_mem,
            composite_score=composite,
        )
