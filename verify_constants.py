import requests

try:
    response = requests.get("http://localhost:8000/api/constants")
    if response.status_code == 200:
        data = response.json()
        print(f"UOM Options from backend: {data.get('uom_options')}")
    else:
        print(f"Failed to fetch constants: {response.status_code}")
except Exception as e:
    print(f"Error: {e}")
