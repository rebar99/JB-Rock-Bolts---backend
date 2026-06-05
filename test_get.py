import urllib.request
import json
import pprint

res = urllib.request.urlopen('http://localhost:8000/api/purchase-orders')
data = json.loads(res.read())
for po in data:
    print(f"PO {po['id']} - {po['po_number']}")
    print(f"  Legacy pending: {po['pending_quantity']}, legacy delivered: {po['delivered_quantity']}")
    for li in po.get('line_items', []):
        print(f"  Item {li['id']}: {li['item']}, qty: {li['quantity']}, del: {li['delivered_quantity']}, pend: {li['pending_quantity']}")
