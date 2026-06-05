import csv

def generate_edge_cases(filename):
    with open(filename, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['sequence', 'order_id', 'type', 'side', 'price', 'quantity'])
        
        seq = 1
        
        # Scenario 1: Empty Book Market Order
        # A market order is submitted when there are no limit orders. Should be rejected or cancelled.
        writer.writerow([seq, 'EDGE_MKT_01', 'MARKET', 'BUY', '', '100'])
        seq += 1
        writer.writerow([seq, 'EDGE_MKT_02', 'MARKET', 'SELL', '', '100'])
        seq += 1
        
        # Build a small book
        writer.writerow([seq, 'LMT_B1', 'LIMIT', 'BUY', '100.00', '10'])
        seq += 1
        writer.writerow([seq, 'LMT_B2', 'LIMIT', 'BUY', '99.00', '20'])
        seq += 1
        writer.writerow([seq, 'LMT_S1', 'LIMIT', 'SELL', '101.00', '10'])
        seq += 1
        writer.writerow([seq, 'LMT_S2', 'LIMIT', 'SELL', '102.00', '20'])
        seq += 1
        
        # Scenario 2: Crossing the spread
        # Limit order priced aggressively to act as a market order
        writer.writerow([seq, 'CROSS_B1', 'LIMIT', 'BUY', '102.00', '15'])
        seq += 1
        
        # Scenario 3: Large Market Sweep
        # Market order that consumes multiple levels of the book
        # Will eat the remaining 15 of LMT_S2 and then should be partially cancelled if no more liquidity
        writer.writerow([seq, 'SWEEP_B1', 'MARKET', 'BUY', '', '1000'])
        seq += 1
        
        # Scenario 4: Canceling a fully filled order
        # CROSS_B1 should be fully filled, so cancelling it should fail
        writer.writerow([seq, 'CROSS_B1', 'CANCEL', '', '', ''])
        seq += 1
        
        # Scenario 5: Canceling a non-existent order
        writer.writerow([seq, 'GHOST_01', 'CANCEL', '', '', ''])
        seq += 1
        
        # Scenario 6: Malformed / Invalid Orders
        # Zero quantity
        writer.writerow([seq, 'INV_Q0', 'LIMIT', 'BUY', '100.00', '0'])
        seq += 1
        # Negative quantity
        writer.writerow([seq, 'INV_QN', 'LIMIT', 'SELL', '100.00', '-10'])
        seq += 1
        # Zero price
        writer.writerow([seq, 'INV_P0', 'LIMIT', 'BUY', '0.00', '10'])
        seq += 1
        # Negative price
        writer.writerow([seq, 'INV_PN', 'LIMIT', 'BUY', '-10.00', '10'])
        seq += 1
        
        # Scenario 7: Time-Price Priority (FIFO)
        writer.writerow([seq, 'FIFO_B1', 'LIMIT', 'BUY', '50.00', '10'])
        seq += 1
        writer.writerow([seq, 'FIFO_B2', 'LIMIT', 'BUY', '50.00', '15'])
        seq += 1
        writer.writerow([seq, 'FIFO_B3', 'LIMIT', 'BUY', '50.00', '5'])
        seq += 1
        
        # Sell that hits exact price, should fill FIFO_B1 first, then B2
        writer.writerow([seq, 'FIFO_S1', 'LIMIT', 'SELL', '50.00', '20'])
        seq += 1

        # Scenario 8: Micro-penny / Tick size check
        writer.writerow([seq, 'TICK_1', 'LIMIT', 'BUY', '50.001', '10'])
        seq += 1
        
        # Scenario 9: Extremely high concurrency (simulated by 1000 same-side orders)
        for i in range(1000):
            writer.writerow([seq, f'CONC_{i}', 'LIMIT', 'BUY', '10.00', '1'])
            seq += 1
            
        # One giant sell to sweep the concurrent orders
        writer.writerow([seq, 'CONC_SWEEP', 'LIMIT', 'SELL', '10.00', '1000'])
        seq += 1

if __name__ == '__main__':
    generate_edge_cases('/home/savvy19/Desktop/iicpc/data/exhaustive_edge_cases_orders.csv')
    print("Generated exhaustive_edge_cases_orders.csv")
