import requests
import json

base_url = "http://localhost:8000"
resp = requests.get(f"{base_url}/purchase-orders/")
if resp.status_code == 200:
    orders = resp.json()
    for o in orders:
        print(f"PO {o.get('id')} ({o.get('po_number')}): Status={o.get('delivery_status')}, AllMarked={o.get('all_dispatches_marked')}")
else:
    print(f"Failed to fetch POs: {resp.status_code}")
