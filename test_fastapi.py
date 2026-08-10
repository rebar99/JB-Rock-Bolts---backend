from fastapi import FastAPI, UploadFile, File
from fastapi.testclient import TestClient
from typing import Optional

app = FastAPI()

@app.post("/import")
def test_import(
    file: UploadFile = File(...),
    created_by: Optional[str] = None
):
    return {"created_by": created_by, "filename": file.filename}

client = TestClient(app)

res = client.post("/import?created_by=Deepika", files={"file": ("test.csv", b"a,b\n1,2", "text/csv")})
print("STATUS:", res.status_code)
print("JSON:", res.json())
