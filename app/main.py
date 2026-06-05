from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from fastapi.staticfiles import StaticFiles
import logging
import os

from app.config import settings
from app.database import create_database_if_not_exists, engine, SessionLocal, Base
from app.models import models  # noqa: F401 — registers all ORM models with Base

from app.routers import dashboard
from app.routers import purchase_orders
from app.routers import sales
from app.routers import inventory
from app.routers import clients
from app.routers import records
from app.routers import reports
from app.routers import users
from app.routers import constants
from app.routers import documents
from app.routers import projects
from app.routers import logs

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting JB Rock Bolts API...")
    create_database_if_not_exists()
    Base.metadata.create_all(bind=engine)
    logger.info("All tables created/verified.")

    from sqlalchemy import text
    try:
        with engine.begin() as conn:
            # Safely migrate purchase_orders.client_id
            try:
                conn.execute(text("SELECT client_id FROM purchase_orders LIMIT 1"))
            except Exception:
                try:
                    conn.execute(text("ALTER TABLE purchase_orders ADD COLUMN client_id INT NULL"))
                except Exception:
                    pass

            # Safely migrate purchase_orders.project_id
            try:
                conn.execute(text("SELECT project_id FROM purchase_orders LIMIT 1"))
            except Exception:
                try:
                    conn.execute(text("ALTER TABLE purchase_orders ADD COLUMN project_id INT NULL"))
                except Exception:
                    pass

            # Safely migrate po_line_items.delivered_quantity
            try:
                conn.execute(text("SELECT delivered_quantity FROM po_line_items LIMIT 1"))
            except Exception:
                try:
                    conn.execute(text("ALTER TABLE po_line_items ADD COLUMN delivered_quantity FLOAT DEFAULT 0"))
                except Exception:
                    pass

            # Safely migrate sales.line_item_id
            try:
                conn.execute(text("SELECT line_item_id FROM sales LIMIT 1"))
            except Exception:
                try:
                    conn.execute(text("ALTER TABLE sales ADD COLUMN line_item_id INT NULL"))
                except Exception:
                    pass
                    
            # Safely add constraints to purchase_orders
            try:
                conn.execute(text("ALTER TABLE purchase_orders ADD CONSTRAINT fk_po_client FOREIGN KEY (client_id) REFERENCES clients(id)"))
            except Exception:
                pass
                
            try:
                conn.execute(text("ALTER TABLE purchase_orders ADD CONSTRAINT fk_po_project FOREIGN KEY (project_id) REFERENCES projects(id)"))
            except Exception:
                pass

            # Safely migrate purchase_orders.file_url
            try:
                conn.execute(text("SELECT file_url FROM purchase_orders LIMIT 1"))
            except Exception:
                try:
                    conn.execute(text("ALTER TABLE purchase_orders ADD COLUMN file_url VARCHAR(500) NULL"))
                except Exception:
                    pass

            # Safely migrate records.client_id
            try:
                conn.execute(text("SELECT client_id FROM records LIMIT 1"))
            except Exception:
                try:
                    conn.execute(text("ALTER TABLE records ADD COLUMN client_id INT NULL"))
                except Exception:
                    pass
                    
            # Safely add constraints to records
            try:
                conn.execute(text("ALTER TABLE records ADD CONSTRAINT fk_rec_client FOREIGN KEY (client_id) REFERENCES clients(id)"))
            except Exception:
                pass

            # Safely migrate sales fields for multi-item support
            for col, dtype in [
                ("subtotal", "FLOAT DEFAULT 0"),
                ("gst_amount", "FLOAT DEFAULT 0"),
                ("grand_total", "FLOAT DEFAULT 0"),
                ("e_way_bill_no", "VARCHAR(100) NULL"),
                ("hsn_code", "VARCHAR(50) NULL"),
                ("e_way_bill_url", "VARCHAR(500) NULL"),
            ]:
                try:
                    conn.execute(text(f"SELECT {col} FROM sales LIMIT 1"))
                except Exception:
                    try:
                        conn.execute(text(f"ALTER TABLE sales ADD COLUMN {col} {dtype}"))
                    except Exception:
                        pass

            for col in ["created_by", "updated_by", "dispatch_from", "ship_to", "bill_to", "dispatched_through", "buyers_order_no", "payment_terms", "item", "uom"]:
                try:
                    conn.execute(text(f"SELECT {col} FROM sales LIMIT 1"))
                    # If it exists, make it nullable (especially for legacy columns like item, uom)
                    if col in ["item", "uom"]:
                        conn.execute(text(f"ALTER TABLE sales MODIFY COLUMN {col} VARCHAR(300) NULL"))
                except Exception:
                    try:
                        conn.execute(text(f"ALTER TABLE sales ADD COLUMN {col} TEXT NULL"))
                    except Exception:
                        pass
            
            for col in ["dispatched_qty", "total_qty", "previous_delivered", "unit_price", "gst_rate"]:
                try:
                    conn.execute(text(f"SELECT {col} FROM sales LIMIT 1"))
                    conn.execute(text(f"ALTER TABLE sales MODIFY COLUMN {col} FLOAT NULL"))
                except Exception:
                    pass
    except Exception as e:
        logger.error(f"Error applying schema updates: {e}")

    db = SessionLocal()
    try:
        from app.services.seed import run_seed
        run_seed(db)
    finally:
        db.close()

    yield
    logger.info("Shutting down JB Rock Bolts API.")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Marketing & Sales Management API for JB Rock Bolts",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Mount local uploads only when R2 is not configured (local development)
if not settings.r2_enabled:
    UPLOAD_DIR = "uploads"
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

app.include_router(constants.router)
app.include_router(dashboard.router)
app.include_router(purchase_orders.router)
app.include_router(sales.router)
app.include_router(inventory.router)
app.include_router(clients.router)
app.include_router(records.router)
app.include_router(reports.router)
app.include_router(users.router)
app.include_router(documents.router)
app.include_router(projects.router)
app.include_router(logs.router)


@app.get("/", tags=["Health"])
def root():
    return {
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "status": "running",
        "docs": "/docs",
    }


@app.get("/health", tags=["Health"])
def health():
    return {"status": "ok"}
