import urllib.request
import json

res = urllib.request.urlopen('http://localhost:8000/api/sales')
data = json.loads(res.read())
for sale in data:
    if sale['po_number'] == '7400094616':
        print(f"Sale {sale['id']} - Invoice: {sale['invoice_number']}")
        for item in sale.get('items', []):
            print(f"  SaleItem: {item['item']}, qty: {item['quantity']}, line_item_id: {item.get('line_item_id')}")
