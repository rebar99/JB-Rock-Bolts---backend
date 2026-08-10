from fastapi import FastAPI, UploadFile, File
from fastapi.testclient import TestClient
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/api/sales/import")
def test_import(
    file: UploadFile = File(...),
):
    return {"filename": file.filename}

client = TestClient(app)

res = client.post("//api/sales/import", files={"file": ("test.csv", b"a,b", "text/csv")})
print("STATUS:", res.status_code)
print("HEADERS:", res.headers)
