import urllib.request
import json
import traceback

def test():
    try:
        # 1. Create a sale for PO 2
        sale_payload = {
            "po_id": 2,
            "po_number": "PO-20260508-193826",
            "client_name": "test",
            "items": [
                {
                    "line_item_id": 101,  # Assuming this exists from previous PUT
                    "item": "MECHANICAL COUPLER IS:16172 25MM DIA",
                    "quantity": 10,
                    "uom": "ZNS",
                    "unit_price": 70,
                    "subtotal": 700,
                    "gst_amount": 126,
                    "total_amount": 826
                }
            ],
            "subtotal": 700,
            "gst_amount": 126,
            "grand_total": 826
        }
        
        req = urllib.request.Request(
            'http://localhost:8000/api/sales', 
            data=json.dumps(sale_payload).encode('utf-8'), 
            headers={'Content-Type': 'application/json'}, 
            method='POST'
        )
        urllib.request.urlopen(req)
        print("Created Sale")

        # 2. Try to update PO 2
        po_payload = {
            "client_name": "test",
            "po_number": "PO-20260508-193826",
            "freight": 0,
            "line_items": [
                {
                    "item": "MECHANICAL COUPLER IS:16172 25MM DIA",
                    "quantity": 2077,
                    "uom": "ZNS",
                    "unit_price": 70
                }
            ]
        }
        
        req2 = urllib.request.Request(
            'http://localhost:8000/api/purchase-orders/2', 
            data=json.dumps(po_payload).encode('utf-8'), 
            headers={'Content-Type': 'application/json'}, 
            method='PUT'
        )
        
        res2 = urllib.request.urlopen(req2)
        print("Updated PO successfully:", res2.status)
        
    except Exception as e:
        print("Failed!")
        traceback.print_exc()
        if hasattr(e, 'read'):
            print("Response body:", e.read().decode())

test()
