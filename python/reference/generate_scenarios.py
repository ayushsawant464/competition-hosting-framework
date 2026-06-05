import csv
import random
import os
from pathlib import Path

from reference.interface import Order, Side, OrderType
from reference.orderbook import ReferenceOrderBook

def generate_scenario(name: str, num_orders: int, seed: int, distribution: dict, output_dir: str = "data"):
    """
    Generates a deterministic sequence of orders and the corresponding expected trades.
    """
    os.makedirs(output_dir, exist_ok=True)
    rng = random.Random(seed)
    ref_book = ReferenceOrderBook()
    
    orders_path = Path(output_dir) / f"{name}_orders.csv"
    expected_path = Path(output_dir) / f"{name}_expected.csv"
    
    active_order_ids = []
    
    with open(orders_path, 'w', newline='') as f_in, open(expected_path, 'w', newline='') as f_out:
        in_writer = csv.writer(f_in)
        out_writer = csv.writer(f_out)
        
        # Headers
        in_writer.writerow(['sequence', 'order_id', 'type', 'side', 'price', 'quantity'])
        out_writer.writerow(['after_sequence', 'taker_id', 'maker_id', 'price', 'quantity', 'taker_side'])
        
        for seq in range(1, num_orders + 1):
            order_id = f"ORD{seq:06d}"
            
            # Determine action based on distribution
            action_roll = rng.random()
            
            if action_roll < distribution.get('cancel', 0.0) and active_order_ids:
                # Cancel an existing order
                cancel_id = rng.choice(active_order_ids)
                active_order_ids.remove(cancel_id)
                in_writer.writerow([seq, cancel_id, "CANCEL", "", "", ""])
                ref_book.cancel_order(cancel_id)
                continue
                
            # It's an ADD order
            side = rng.choice([Side.BUY, Side.SELL])
            
            # Determine order type (LIMIT vs MARKET)
            type_roll = rng.random()
            is_market = type_roll < distribution.get('market', 0.0)
            order_type = OrderType.MARKET if is_market else OrderType.LIMIT
            
            # Generate price around a starting mid price of 100.0
            if order_type == OrderType.LIMIT:
                base_price = 100.0
                if side == Side.BUY:
                    price = round(rng.uniform(base_price - 2.0, base_price + 0.5), 2)
                else:
                    price = round(rng.uniform(base_price - 0.5, base_price + 2.0), 2)
            else:
                price = None
                
            quantity = rng.randint(1, 100)
            
            order = Order(order_id=order_id, side=side, order_type=order_type, price=price, quantity=quantity)
            
            # Write to input CSV
            in_writer.writerow([
                seq, 
                order.order_id, 
                order.order_type.value, 
                order.side.value, 
                order.price if order.price else "", 
                order.quantity
            ])
            
            # Track active limit orders for potential cancellation
            if order.order_type == OrderType.LIMIT:
                active_order_ids.append(order.order_id)
                
            # Process through reference engine to get expected trades
            trades = ref_book.add_order(order)
            
            # If the limit order was completely filled, it's not active anymore
            if order.order_type == OrderType.LIMIT and order.quantity == 0:
                active_order_ids.remove(order.order_id)
                
            # Update active orders from trades (makers getting filled)
            for trade in trades:
                out_writer.writerow([
                    seq,
                    trade.taker_id,
                    trade.maker_id,
                    f"{trade.price:.2f}",
                    trade.quantity,
                    trade.taker_side.value
                ])
                # If maker is filled completely, it would be removed from reference book
                # We can't perfectly track maker fills in active_order_ids without querying the book,
                # but for generation purposes, a failed cancel is acceptable (simulates real-world race conditions).

def generate_all_scenarios():
    """Generates the predefined dataset for the hackathon."""
    print("Generating warmup scenario...")
    generate_scenario("warmup", 100, seed=42, distribution={'market': 0.2, 'cancel': 0.1})
    
    print("Generating standard benchmark scenario...")
    generate_scenario("standard", 10000, seed=42, distribution={'market': 0.1, 'cancel': 0.2})
    
    print("Generating stress scenario...")
    generate_scenario("stress", 50000, seed=42, distribution={'market': 0.3, 'cancel': 0.3})
    
    print("Generating flash crash scenario...")
    # Heavy market sells
    generate_scenario("flash_crash", 20000, seed=42, distribution={'market': 0.5, 'cancel': 0.05})
    
    print("Generating cancel storm scenario...")
    generate_scenario("cancel_storm", 20000, seed=42, distribution={'market': 0.05, 'cancel': 0.7})
    
    print("All scenarios generated successfully in data/")

if __name__ == "__main__":
    generate_all_scenarios()
