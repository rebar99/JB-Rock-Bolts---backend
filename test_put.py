import urllib.request
import json
import traceback

def test():
    try:
        # Get PO 2
        res = urllib.request.urlopen('http://localhost:8000/api/purchase-orders')
        data = json.loads(res.read())
        po = data[0]
        
        # Build payload similar to frontend
        payload = {
            "client_name": po["client_name"],
            "po_number": po["po_number"],
            "project": po["project"],
            "gst": po["gst"],
            "freight": float(po["freight"]),
            "payment_terms": po["payment_terms"],
            "validity_date": po["validity_date"],
            "file_url": po["file_url"],
            "line_items": [
                {
                    "item": "MECHANICAL COUPLER IS:16172 25MM DIA",
                    "quantity": 2077,
                    "uom": "ZNS",
                    "unit_price": 70
                },
                {
                    "item": "MECHANICAL COUPLER IS:16172 36MM DIA",
                    "quantity": 2077,
                    "uom": "ZNS",
                    "unit_price": 195
                }
            ]
        }
        
        req = urllib.request.Request(
            'http://localhost:8000/api/purchase-orders/' + str(po['id']), 
            data=json.dumps(payload).encode('utf-8'), 
            headers={'Content-Type': 'application/json'}, 
            method='PUT'
        )
        
        res = urllib.request.urlopen(req)
        print("Success:", res.status)
        print(res.read().decode())
    except Exception as e:
        print("Failed!")
        traceback.print_exc()
        if hasattr(e, 'read'):
            print(e.read().decode())

test()
