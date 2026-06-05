import urllib.request
import json

try:
    with urllib.request.urlopen("http://localhost:8000/api/constants") as response:
        data = json.loads(response.read().decode())
        print(f"UOM Options from backend: {data.get('uom_options')}")
except Exception as e:
    print(f"Error: {e}")
