from dataclasses import dataclass
from enum import Enum
from typing import Optional, List

class Side(Enum):
    BUY = "BUY"
    SELL = "SELL"

class OrderType(Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"

@dataclass
class Order:
    order_id: str
    side: Side
    order_type: OrderType
    price: Optional[float]
    quantity: int

@dataclass
class Trade:
    taker_id: str
    maker_id: str
    price: float
    quantity: int
    taker_side: Side

# Contestants do NOT need to subclass this if we use network sockets,
# but it's useful for the reference engine.
class OrderBookInterface:
    def add_order(self, order: Order) -> List[Trade]:
        raise NotImplementedError
        
    def cancel_order(self, order_id: str) -> bool:
        raise NotImplementedError
        
    def get_best_bid(self) -> Optional[float]:
        raise NotImplementedError
        
    def get_best_ask(self) -> Optional[float]:
        raise NotImplementedError
