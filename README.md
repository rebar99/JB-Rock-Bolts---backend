# JB Rock Bolts — Backend

FastAPI + MySQL REST API powering the JB Rock Bolts marketing & sales dashboard.  
The database and all tables are created automatically on first startup. Seed data is inserted on first run.

## Tech Stack

| Layer | Library |
|---|---|
| Framework | FastAPI 0.115 |
| ORM | SQLAlchemy 2.0 |
| Database | MySQL 8+ (via PyMySQL) |
| Validation | Pydantic v2 |
| Auth | python-jose (JWT) + passlib (bcrypt) |
| Server | Uvicorn |

## API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/api/constants` | Products, clients, UOM, projects, payment terms |
| GET | `/api/dashboard/stats` | KPI totals |
| GET | `/api/dashboard/charts` | Chart data (bar, pie, line) |
| GET | `/api/dashboard/recent-sales` | Last N sales |
| GET/POST | `/api/purchase-orders` | List / create POs |
| GET/PUT/DELETE | `/api/purchase-orders/{id}` | Read / update / delete PO |
| GET/POST | `/api/sales` | List / create sales |
| GET/PUT/DELETE | `/api/sales/{id}` | Read / update / delete sale |
| POST | `/api/sales/{id}/activities` | Append activity log entry |
| GET/POST/PUT/DELETE | `/api/inventory` | Inventory CRUD |
| GET/POST/DELETE | `/api/clients` | Client CRUD |
| GET/POST/PUT/DELETE | `/api/records` | Records CRUD |
| GET | `/api/reports` | Filtered report with CSV-ready data |
| POST | `/api/users/register` | Register user |
| POST | `/api/users/login` | Login → JWT token |
| GET | `/api/documents/po/{id}` | HTML PO receipt (printable) |
| GET | `/api/documents/invoice/{id}` | HTML sales invoice (printable) |

Interactive docs: `http://localhost:8000/docs`

## Prerequisites

- Python 3.11+
- MySQL 8.0+ running locally or via Docker

## Setup

```bash
# 1. Change into the backend folder
cd JB-Rock-Bolts---backend

# 2. Create virtual environment
python -m venv .venv
.venv\Scripts\activate      # Windows PowerShell
# OR: source .venv/bin/activate     # macOS/Linux

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
copy .env.example .env       # Windows
# OR: cp .env.example .env   # macOS/Linux
# Edit .env — set DB_PASSWORD and SECRET_KEY at minimum

# 5. Start the server
.\.venv\Scripts\Activate.ps1      # Windows PowerShell
python -m uvicorn app.main:app --reload --port 8000
```

> You can also run directly from the repository root:
> ```powershell
> D:\jbrocks1\JB-Rock-Bolts---backend\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
> ```
> Use the backend `.venv` because `D:\jbrocks1\.venv` may not contain the required packages.

The server will:
1. Connect to MySQL and create the database if it doesn't exist
2. Create all tables via SQLAlchemy `create_all`
3. Seed 25 clients, 11 products, sample POs and records on first run

Default admin credentials (seeded):
- Email: `admin@jbrockbolts.com`
- Password: `admin@123`

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DB_HOST` | `localhost` | MySQL host |
| `DB_PORT` | `3306` | MySQL port |
| `DB_NAME` | `jbrockbolts_db` | Database name |
| `DB_USER` | `root` | MySQL user |
| `DB_PASSWORD` | — | MySQL password (**required**) |
| `SECRET_KEY` | — | JWT signing key (**required in prod**) |
| `CORS_ORIGINS` | `http://localhost:8080,...` | Allowed CORS origins |
| `DEBUG` | `True` | Enable SQLAlchemy echo |
| `PORT` | `8000` | Server port |

## Docker

```bash
# Build image
docker build -t jbrockbolts-backend .

# Run (pass env vars)
docker run -p 8000:8000 \
  -e DB_HOST=host.docker.internal \
  -e DB_PASSWORD=yourpassword \
  -e SECRET_KEY=your-secret-key \
  jbrockbolts-backend
```

## Project Structure

```
app/
├── main.py           # FastAPI app, CORS, startup lifecycle
├── config.py         # Pydantic Settings (reads .env)
├── database.py       # Engine, SessionLocal, auto-create DB
├── models/
│   └── models.py     # SQLAlchemy ORM models (7 tables)
├── schemas/          # Pydantic request/response schemas
│   ├── purchase_order.py
│   ├── sale.py
│   ├── record.py
│   ├── product.py
│   ├── client.py
│   ├── user.py
│   ├── dashboard.py
│   └── reports.py
├── routers/          # FastAPI route handlers
│   ├── constants.py      # GET /api/constants
│   ├── dashboard.py
│   ├── purchase_orders.py
│   ├── sales.py
│   ├── inventory.py
│   ├── clients.py
│   ├── records.py
│   ├── reports.py
│   ├── users.py
│   └── documents.py      # HTML document generation
├── services/
│   └── seed.py       # Initial data seeder
└── utils/
    └── helpers.py    # Invoice numbering, financial calculations
```

## Database Schema

| Table | Description |
|---|---|
| `users` | Application users (name, email, bcrypt password) |
| `clients` | Client companies with location |
| `products` | Inventory items with stock quantity |
| `purchase_orders` | POs with quantities, pricing, activity timestamps |
| `sales` | Dispatch records linked to POs, with invoice numbers |
| `sale_activities` | Audit trail for each sale |
| `records` | Dashboard sales records (summary data) |
