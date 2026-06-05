from typing import List, Optional, Dict
from reference.interface import OrderBookInterface, Order, Trade, Side, OrderType

class ReferenceOrderBook(OrderBookInterface):
    """
    Reference implementation.
    Optimized for correctness (strict price-time priority), not necessarily speed.
    """
    def __init__(self):
        # Maps price -> list of orders
        self.bids: Dict[float, List[Order]] = {}
        self.asks: Dict[float, List[Order]] = {}
        # Maps order_id -> (price, side, order_object)
        self.orders: Dict[str, tuple] = {}
        
    def add_order(self, order: Order) -> List[Trade]:
        trades = []
        
        if order.side == Side.BUY:
            trades = self._match(order, self.asks, reverse=False) # Match against asks (lowest first)
            if order.quantity > 0 and order.order_type == OrderType.LIMIT:
                if order.price not in self.bids:
                    self.bids[order.price] = []
                self.bids[order.price].append(order)
                self.orders[order.order_id] = (order.price, Side.BUY, order)
                
        else: # SELL
            trades = self._match(order, self.bids, reverse=True) # Match against bids (highest first)
            if order.quantity > 0 and order.order_type == OrderType.LIMIT:
                if order.price not in self.asks:
                    self.asks[order.price] = []
                self.asks[order.price].append(order)
                self.orders[order.order_id] = (order.price, Side.SELL, order)
                
        return trades

    def _match(self, order: Order, book: Dict[float, List[Order]], reverse: bool) -> List[Trade]:
        trades = []
        
        while order.quantity > 0:
            if not book:
                break
                
            # Find best price
            prices = sorted(book.keys(), reverse=reverse)
            best_price = prices[0]
            
            # Check price limit
            if order.order_type == OrderType.LIMIT:
                if order.side == Side.BUY and best_price > order.price:
                    break
                if order.side == Side.SELL and best_price < order.price:
                    break
                    
            # Match with orders at best price
            queue = book[best_price]
            while queue and order.quantity > 0:
                maker = queue[0]
                trade_qty = min(order.quantity, maker.quantity)
                
                trades.append(Trade(
                    taker_id=order.order_id,
                    maker_id=maker.order_id,
                    price=best_price,
                    quantity=trade_qty,
                    taker_side=order.side
                ))
                
                order.quantity -= trade_qty
                maker.quantity -= trade_qty
                
                if maker.quantity == 0:
                    queue.pop(0)
                    del self.orders[maker.order_id]
                    
            if not queue:
                del book[best_price]
                
        return trades

    def cancel_order(self, order_id: str) -> bool:
        if order_id not in self.orders:
            return False
            
        price, side, order = self.orders[order_id]
        book = self.bids if side == Side.BUY else self.asks
        
        if price in book:
            book[price].remove(order)
            if not book[price]:
                del book[price]
                
        del self.orders[order_id]
        return True

    def get_best_bid(self) -> Optional[float]:
        if not self.bids:
            return None
        return max(self.bids.keys())

    def get_best_ask(self) -> Optional[float]:
        if not self.asks:
            return None
        return min(self.asks.keys())
