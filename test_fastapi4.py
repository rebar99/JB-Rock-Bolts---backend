from fastapi import FastAPI, UploadFile, File
from fastapi.testclient import TestClient
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/api/sales/import")
def test_import(
    file: UploadFile = File(...),
):
    raise RuntimeError("Crashing!")

client = TestClient(app)
res = client.post("/api/sales/import", headers={"Origin": "http://localhost:5173"}, files={"file": ("test.csv", b"a,b", "text/csv")})
print("STATUS:", res.status_code)
print("HEADERS:", res.headers)
