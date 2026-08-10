import httpx
import sys

try:
    res = httpx.get("http://127.0.0.1:8000/api/dashboard/stats")
    print("STATUS:", res.status_code)
    print("JSON:", res.json())
except Exception as e:
    print("ERROR:", e)
