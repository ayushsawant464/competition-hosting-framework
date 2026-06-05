import io
import os
from typing import Dict, Optional

class VirtualFile(io.RawIOBase):
    """
    Mocks a file object in the virtual file system.
    Charges the virtual clock and emulator state for disk I/O operations.
    """
    def __init__(self, filename: str, vfs: 'VirtualFileSystem', mode: str = 'r'):
        self.filename = filename
        self.vfs = vfs
        self.mode = mode
        
        # NVMe SSD Latency Model
        self.SEEK_LATENCY_NS = 10_000        # 10 microsecond seek/syscall latency
        self.WRITE_CYCLES_PER_BYTE = 2       # DMA transfer CPU overhead
        self.READ_CYCLES_PER_BYTE = 1
        
        if 'w' in mode:
            self.vfs.storage[filename] = bytearray()
            self.cursor = 0
        elif 'a' in mode:
            if filename not in self.vfs.storage:
                self.vfs.storage[filename] = bytearray()
            self.cursor = len(self.vfs.storage[filename])
        else: # read mode
            if filename not in self.vfs.storage:
                raise FileNotFoundError(f"[Errno 2] No such file or directory: '{filename}'")
            self.cursor = 0
            
    def read(self, size: int = -1) -> bytes:
        if 'r' not in self.mode and '+' not in self.mode:
            raise io.UnsupportedOperation("not readable")
            
        # Hardware latency
        if self.vfs.clock:
            self.vfs.clock.add_network_delay(self.SEEK_LATENCY_NS)
            
        data_store = self.vfs.storage[self.filename]
        if size < 0 or size is None:
            data = data_store[self.cursor:]
        else:
            data = data_store[self.cursor:self.cursor + size]
            
        self.cursor += len(data)
        
        # CPU Overhead
        if self.vfs.emu_state:
            # Syscall overhead + DMA byte transfer
            self.vfs.emu_state.increment(500 + len(data) * self.READ_CYCLES_PER_BYTE)
            
        return bytes(data)
        
    def write(self, b: bytes) -> int:
        if 'w' not in self.mode and 'a' not in self.mode and '+' not in self.mode:
            raise io.UnsupportedOperation("not writable")
            
        # Hardware latency
        if self.vfs.clock:
            self.vfs.clock.add_network_delay(self.SEEK_LATENCY_NS)
            
        data_store = self.vfs.storage[self.filename]
        
        # Expand buffer if writing past current length
        if self.cursor + len(b) > len(data_store):
            data_store.extend(b'\x00' * (self.cursor + len(b) - len(data_store)))
            
        data_store[self.cursor:self.cursor + len(b)] = b
        self.cursor += len(b)
        
        # CPU Overhead
        if self.vfs.emu_state:
            # Syscall overhead + DMA byte transfer
            self.vfs.emu_state.increment(500 + len(b) * self.WRITE_CYCLES_PER_BYTE)
            
        return len(b)
        
    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        if self.vfs.clock:
            self.vfs.clock.add_network_delay(self.SEEK_LATENCY_NS)
        if self.vfs.emu_state:
            self.vfs.emu_state.increment(100)
            
        data_store = self.vfs.storage[self.filename]
        if whence == os.SEEK_SET:
            self.cursor = offset
        elif whence == os.SEEK_CUR:
            self.cursor += offset
        elif whence == os.SEEK_END:
            self.cursor = len(data_store) + offset
            
        self.cursor = max(0, min(self.cursor, len(data_store)))
        return self.cursor
        
    def close(self):
        pass


class VirtualFileSystem:
    """
    Manages the mocked storage system for a sandbox environment.
    """
    def __init__(self, emu_state=None, clock=None):
        self.emu_state = emu_state
        self.clock = clock
        self.storage: Dict[str, bytearray] = {}  # filename -> file content
        
    def open(self, filename: str, mode: str = 'r', *args, **kwargs):
        """
        Mock replacement for the built-in `open` function.
        """
        # If the file exists in the real OS, don't let them read it unless it's in our VFS.
        # This guarantees sandbox security.
        return VirtualFile(filename, self, mode)
