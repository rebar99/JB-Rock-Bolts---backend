from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
resp = client.get('/api/reports/pending-pos')
print('Status:', resp.status_code)
print(resp.text)
