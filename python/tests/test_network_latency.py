"""Layer 2 Component Tests: InMemoryConnection with latency and I/O tracking."""
import pytest
from emu.environment import EmulationState
from emu.clock import VirtualClock
from emu.virtual_network import InMemoryConnection


class TestRecvLatency:
    """Black-box: verify recv() advances clock and tracks I/O."""

    def test_recv_adds_nic_delay(self):
        """After recv(), clock should have advanced by >= 200ns (NIC delay)."""
        state = EmulationState()
        clock = state.clock
        conn = InMemoryConnection(state, input_bytes=b"hello\n", clock=clock)
        initial_ns = clock.stamp()
        conn.recv(1024)
        assert clock.stamp() > initial_ns
        # At minimum: 200ns NIC + 500 cycles / 3GHz ≈ 166ns = 366ns
        assert clock.stamp() >= initial_ns + 200

    def test_recv_count_tracks_calls(self):
        """5 recv() calls → recv_count == 5 (assuming data available)."""
        state = EmulationState()
        conn = InMemoryConnection(state, input_bytes=b"A" * 250, clock=state.clock)
        for _ in range(5):
            data = conn.recv(1024)
            if not data:
                break
        assert conn.recv_count == 5

    def test_bytes_received_tracked(self):
        state = EmulationState()
        conn = InMemoryConnection(state, input_bytes=b"hello", clock=state.clock)
        total = 0
        while True:
            data = conn.recv(1024)
            if not data:
                break
            total += len(data)
        assert conn.bytes_received == 5
        assert total == 5


class TestSendLatency:
    """Black-box: verify sendall() stamps egress and records latency."""

    def test_send_adds_egress_delay(self):
        state = EmulationState()
        clock = state.clock
        conn = InMemoryConnection(state, input_bytes=b"", clock=clock)
        initial_ns = clock.stamp()
        conn.sendall(b"response\n")
        assert clock.stamp() > initial_ns

    def test_order_latency_recorded(self):
        """recv → sendall with newline → latency recorded."""
        state = EmulationState()
        clock = state.clock
        conn = InMemoryConnection(state, input_bytes=b"order1\n", clock=clock)
        # Recv the order (stamps ingress)
        data = b""
        while True:
            chunk = conn.recv(1024)
            if not chunk:
                break
            data += chunk
        # Send response (stamps egress)
        conn.sendall(b"trade1\n")
        assert len(conn.order_latencies) == 1
        assert conn.order_latencies[0] > 0


class TestFragmentation:
    """White-box: verify fragmentation still works correctly."""

    def test_fragmentation_occurs(self):
        """200 bytes should arrive in multiple fragments."""
        state = EmulationState()
        conn = InMemoryConnection(state, input_bytes=b"X" * 200, clock=state.clock)
        chunks = []
        while True:
            data = conn.recv(1024)
            if not data:
                break
            chunks.append(data)
        assert len(chunks) > 1  # Must be fragmented
        assert b"".join(chunks) == b"X" * 200  # All data arrives

    def test_io_efficiency_ratio(self):
        """bytes_received / recv_count = avg fragment size."""
        state = EmulationState()
        conn = InMemoryConnection(state, input_bytes=b"Y" * 200, clock=state.clock)
        while conn.recv(1024):
            pass
        avg = conn.bytes_received / conn.recv_count
        assert 1 <= avg <= 50  # Fragmented between 1-50 bytes
