import pytest

from reference.interface import Order, Side, OrderType, Trade
from reference.orderbook import ReferenceOrderBook

def test_add_limit_orders():
    ob = ReferenceOrderBook()
    ob.add_order(Order("1", Side.BUY, OrderType.LIMIT, price=100.0, quantity=10))
    ob.add_order(Order("2", Side.SELL, OrderType.LIMIT, price=101.0, quantity=5))
    assert ob.get_best_bid() == 100.0
    assert ob.get_best_ask() == 101.0

def test_market_order_execution():
    ob = ReferenceOrderBook()
    ob.add_order(Order("1", Side.SELL, OrderType.LIMIT, price=100.0, quantity=10))
    trades = ob.add_order(Order("2", Side.BUY, OrderType.MARKET, price=None, quantity=5))
    assert len(trades) == 1
    assert trades[0].quantity == 5
    assert trades[0].price == 100.0
