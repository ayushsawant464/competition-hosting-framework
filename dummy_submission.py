import emu.network as socket
import json

s = socket.Socket(socket.AF_INET, socket.SOCK_STREAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(('0.0.0.0', 8080))
s.listen(1)

conn, addr = s.accept()

buffer = ""
while True:
    data = conn.recv(1024)
    if not data:
        break
        
    buffer += data.decode('utf-8')
    while "\n" in buffer:
        line, buffer = buffer.split("\n", 1)
        line = line.strip()
        if line:
            order = json.loads(line)
            # Create dummy trade
            trade = {
                "after_sequence": order.get("sequence"),
                "taker_id": order.get("order_id"),
                "maker_id": "DUMMY",
                "price": order.get("price", "100.00"),
                "quantity": order.get("quantity", "10"),
                "taker_side": order.get("side", "BUY")
            }
            conn.sendall((json.dumps(trade) + "\n").encode('utf-8'))
            
conn.close()

