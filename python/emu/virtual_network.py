import random


class InMemoryConnection:
    def __init__(self, emu_state, input_bytes=b"", clock=None):
        random.seed(42)  # Ensure deterministic network fragmentation sequence
        self.emu_state = emu_state
        self.clock = clock
        self.input_bytes = input_bytes
        self.output_bytes = bytearray()
        self.read_cursor = 0

        # Latency tracking (per-order)
        self.order_latencies = []
        self._current_ingress_ns = None

        # I/O tracking
        self.recv_count = 0
        self.bytes_received = 0

    def recv(self, bufsize, flags=0):
        # Network delay
        if self.clock:
            self.clock.add_network_delay(200)  # PCI-e bus read: 200 ns
        if self.emu_state:
            self.emu_state.increment(500)

        remaining = len(self.input_bytes) - self.read_cursor
        if remaining <= 0:
            return b""

        # Stamp ingress on first byte of a new order
        if self._current_ingress_ns is None and self.clock:
            self._current_ingress_ns = self.clock.stamp()

        # Simulate fragmentation (max 50 bytes per read)
        fragment_size = random.randint(1, min(bufsize, 50, remaining))
        data = self.input_bytes[self.read_cursor:self.read_cursor + fragment_size]
        self.read_cursor += fragment_size

        # I/O metrics
        self.recv_count += 1
        self.bytes_received += len(data)

        return data

    def send(self, data, flags=0):
        if self.clock:
            self.clock.add_network_delay(100)  # NIC DMA write: 100 ns
        if self.emu_state:
            self.emu_state.increment(200)
        self.output_bytes.extend(data)
        return len(data)

    def sendall(self, data, flags=0):
        if self.clock:
            self.clock.add_network_delay(100)
        if self.emu_state:
            self.emu_state.increment(200 + len(data) * 2)
        self.output_bytes.extend(data)

        # If this send contains a newline, an order response is complete
        if b"\n" in data and self._current_ingress_ns is not None and self.clock:
            egress_ns = self.clock.stamp()
            latency = egress_ns - self._current_ingress_ns
            self.order_latencies.append(latency)
            self._current_ingress_ns = None  # Reset for next order

        return None

    def close(self):
        pass

    def settimeout(self, value):
        pass


class InMemoryServerSocket:
    """
    Mocks a server socket. When accept() is called, it returns a predefined
    InMemoryConnection that has the CSV data pre-loaded.
    """
    AF_INET = 2
    SOCK_STREAM = 1
    SOL_SOCKET = 65535
    SO_REUSEADDR = 2

    def __init__(self, emu_state, preloaded_conn=None):
        self.emu_state = emu_state
        self.preloaded_conn = preloaded_conn
        self.accepted = False

    def bind(self, address):
        pass

    def listen(self, backlog=None):
        pass

    def accept(self):
        if self.accepted:
            raise BlockingIOError("Mock only supports one connection")
        self.accepted = True
        return self.preloaded_conn, ("127.0.0.1", 12345)

    def setsockopt(self, level, optname, value):
        pass

    def close(self):
        pass

    def settimeout(self, value):
        pass


def create_socket_factory(emu_state, preloaded_conn):
    def socket_factory_inner(family=2, type=1, proto=-1, fileno=None):
        return InMemoryServerSocket(emu_state, preloaded_conn)

    socket_factory_inner.AF_INET = InMemoryServerSocket.AF_INET
    socket_factory_inner.SOCK_STREAM = InMemoryServerSocket.SOCK_STREAM
    socket_factory_inner.SOL_SOCKET = InMemoryServerSocket.SOL_SOCKET
    socket_factory_inner.SO_REUSEADDR = InMemoryServerSocket.SO_REUSEADDR
    return socket_factory_inner
