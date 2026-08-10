import requests
import io

url = "http://127.0.0.1:8000/api/sales/import"
files = {'file': ('test.csv', io.BytesIO(b"invoice_number,po_number\n123,456"), 'text/csv')}
try:
    response = requests.post(url, files=files, params={"on_conflict": "skip", "created_by": "Test"})
    print(response.status_code)
    print(response.text)
except Exception as e:
    print(f"Exception: {e}")
