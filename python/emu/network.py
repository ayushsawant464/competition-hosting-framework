"""
Public network API for competitors.
Use: import emu.network as network
"""
import sys
from emu.virtual_network import InMemoryServerSocket

class Socket:
    def __new__(cls, family=2, type=1, proto=-1, fileno=None):
        _state = sys.modules.get('__emu__')
        if _state and hasattr(_state, 'preloaded_conn'):
            return InMemoryServerSocket(_state, _state.preloaded_conn)
        else:
            return InMemoryServerSocket(None, None)

    AF_INET = 2
    SOCK_STREAM = 1
    SOL_SOCKET = 65535
    SO_REUSEADDR = 2

AF_INET = 2
SOCK_STREAM = 1
SOL_SOCKET = 65535
SO_REUSEADDR = 2
