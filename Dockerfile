# ── Backend: FastAPI + Python 3.12 ────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# System deps for mysqlclient / cryptography
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc default-libmysqlclient-dev pkg-config \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render sets PORT automatically; default to 8000 locally
EXPOSE 8000

CMD sh -c "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"
