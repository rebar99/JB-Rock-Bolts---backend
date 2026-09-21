from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from fastapi.staticfiles import StaticFiles
import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta

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
from app.routers import work_orders
from app.routers import work_order_reports
from app.routers import work_order_sales
from app.routers import item_master
from app.routers import uom
from app.routers import company_addresses
from app.routers import system
from app.routers import recently_deleted
from app.routers import credit_notes

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app import notifications
    notifications.init_loop(asyncio.get_running_loop())
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

            # Safely migrate sales.invoice_date
            try:
                conn.execute(text("SELECT invoice_date FROM sales LIMIT 1"))
            except Exception:
                try:
                    conn.execute(text("ALTER TABLE sales ADD COLUMN invoice_date DATE NULL"))
                except Exception:
                    pass

            # Safely migrate work_order_sales.invoice_date
            try:
                conn.execute(text("SELECT invoice_date FROM work_order_sales LIMIT 1"))
            except Exception:
                try:
                    conn.execute(text("ALTER TABLE work_order_sales ADD COLUMN invoice_date DATE NULL"))
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

            # Safely migrate work_orders.file_url
            try:
                conn.execute(text("SELECT file_url FROM work_orders LIMIT 1"))
            except Exception:
                try:
                    conn.execute(text("ALTER TABLE work_orders ADD COLUMN file_url VARCHAR(500) NULL"))
                except Exception:
                    pass

            # Safely migrate purchase_orders.remark
            try:
                conn.execute(text("SELECT remark FROM purchase_orders LIMIT 1"))
            except Exception:
                try:
                    conn.execute(text("ALTER TABLE purchase_orders ADD COLUMN remark TEXT NULL"))
                except Exception:
                    pass

            # Safely migrate purchase_orders.po_date / validity_date
            for pd_col, pd_dtype in [
                ("po_date", "DATETIME NULL"),
                ("validity_date", "DATETIME NULL"),
            ]:
                try:
                    conn.execute(text(f"SELECT {pd_col} FROM purchase_orders LIMIT 1"))
                except Exception:
                    try:
                        conn.execute(text(f"ALTER TABLE purchase_orders ADD COLUMN {pd_col} {pd_dtype}"))
                    except Exception:
                        pass

            # Safely migrate purchase_orders short_closed columns
            for sc_col, sc_dtype in [
                ("short_closed", "BOOLEAN DEFAULT FALSE NOT NULL"),
                ("short_closed_at", "DATETIME NULL"),
                ("short_closed_by", "VARCHAR(100) NULL"),
                ("short_closed_remark", "TEXT NULL"),
            ]:
                try:
                    conn.execute(text(f"SELECT {sc_col} FROM purchase_orders LIMIT 1"))
                except Exception:
                    try:
                        conn.execute(text(f"ALTER TABLE purchase_orders ADD COLUMN {sc_col} {sc_dtype}"))
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
            # Safely migrate system_logs new columns
            for col, dtype in [
                ("entity_name", "VARCHAR(300) NULL"),
                ("changed_fields", "TEXT NULL"),
                ("status", "VARCHAR(50) NULL DEFAULT 'Success'"),
            ]:
                try:
                    conn.execute(text(f"SELECT {col} FROM system_logs LIMIT 1"))
                except Exception:
                    try:
                        conn.execute(text(f"ALTER TABLE system_logs ADD COLUMN {col} {dtype}"))
                    except Exception:
                        pass

            # Existing users predate the approval workflow — grandfather them in as active
            try:
                conn.execute(text("UPDATE users SET is_active = TRUE WHERE is_active IS NULL"))
            except Exception:
                pass

            # Safely create user_sessions table
            try:
                conn.execute(text("SELECT id FROM user_sessions LIMIT 1"))
            except Exception:
                try:
                    conn.execute(text("""
                        CREATE TABLE user_sessions (
                            id INT AUTO_INCREMENT PRIMARY KEY,
                            user_id INT NOT NULL,
                            user_name VARCHAR(100) NOT NULL,
                            user_email VARCHAR(150) NOT NULL,
                            login_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                            logout_at DATETIME NULL,
                            is_active BOOLEAN NOT NULL DEFAULT TRUE,
                            FOREIGN KEY (user_id) REFERENCES users(id)
                        )
                    """))
                except Exception:
                    pass

            # ── Soft-delete columns for recently deleted feature ────────────────
            for tbl in ["sales", "purchase_orders", "work_orders", "work_order_sales"]:
                for col, dtype in [
                    ("is_deleted", "BOOLEAN NOT NULL DEFAULT FALSE"),
                    ("deleted_at", "DATETIME NULL"),
                    ("deleted_by", "VARCHAR(100) NULL"),
                    ("permanent_delete_at", "DATETIME NULL"),
                ]:
                    try:
                        conn.execute(text(f"SELECT {col} FROM {tbl} LIMIT 1"))
                    except Exception:
                        try:
                            conn.execute(text(f"ALTER TABLE {tbl} ADD COLUMN {col} {dtype}"))
                        except Exception:
                            pass

            # Credit notes use the same 24-hour Recently Deleted lifecycle.
            for col, dtype in [
                ("is_deleted", "BOOLEAN NOT NULL DEFAULT FALSE"),
                ("deleted_at", "DATETIME NULL"),
                ("deleted_by", "VARCHAR(100) NULL"),
                ("permanent_delete_at", "DATETIME NULL"),
            ]:
                try:
                    conn.execute(text(f"SELECT {col} FROM credit_notes LIMIT 1"))
                except Exception:
                    try:
                        conn.execute(text(f"ALTER TABLE credit_notes ADD COLUMN {col} {dtype}"))
                    except Exception:
                        pass

            # ── Credit Notes tables (created by Base.metadata.create_all above,
            #    but safe-create here for clarity and idempotency) ──────────────
            try:
                conn.execute(text("SELECT id FROM credit_notes LIMIT 1"))
            except Exception:
                try:
                    conn.execute(text("""
                        CREATE TABLE IF NOT EXISTS credit_notes (
                            id INT AUTO_INCREMENT PRIMARY KEY,
                            cn_number VARCHAR(50) NOT NULL UNIQUE,
                            cn_date DATE NOT NULL,
                            sale_type VARCHAR(5) NOT NULL,
                            sale_id INT NULL,
                            wo_sale_id INT NULL,
                            invoice_number VARCHAR(50) NULL,
                            invoice_date DATE NULL,
                            po_number VARCHAR(100) NULL,
                            client_name VARCHAR(200) NOT NULL,
                            project VARCHAR(300) NULL,
                            reason VARCHAR(100) NOT NULL,
                            taxable_amount DECIMAL(12,2) NOT NULL DEFAULT 0,
                            gst_amount DECIMAL(12,2) NOT NULL DEFAULT 0,
                            total_amount DECIMAL(12,2) NOT NULL DEFAULT 0,
                            status VARCHAR(20) NOT NULL DEFAULT 'Issued',
                            created_by VARCHAR(100) NULL,
                            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                            updated_by VARCHAR(100) NULL,
                            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                            is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
                            deleted_at DATETIME NULL,
                            deleted_by VARCHAR(100) NULL,
                            permanent_delete_at DATETIME NULL
                        )
                    """))
                    conn.execute(text("""
                        CREATE TABLE IF NOT EXISTS credit_note_items (
                            id INT AUTO_INCREMENT PRIMARY KEY,
                            credit_note_id INT NOT NULL,
                            item VARCHAR(300) NOT NULL,
                            uom VARCHAR(50) NOT NULL DEFAULT 'Nos',
                            original_qty FLOAT NOT NULL DEFAULT 0,
                            credit_qty FLOAT NOT NULL DEFAULT 0,
                            unit_price DECIMAL(12,2) NOT NULL DEFAULT 0,
                            gst_rate FLOAT NOT NULL DEFAULT 0,
                            subtotal DECIMAL(12,2) NOT NULL DEFAULT 0,
                            gst_amount DECIMAL(12,2) NOT NULL DEFAULT 0,
                            total_amount DECIMAL(12,2) NOT NULL DEFAULT 0,
                            FOREIGN KEY (credit_note_id) REFERENCES credit_notes(id)
                        )
                    """))
                except Exception as ce:
                    logger.warning(f"Credit notes table creation: {ce}")

            # Historical/manual credit notes retain their original invoice date.
            try:
                conn.execute(text("SELECT invoice_date FROM credit_notes LIMIT 1"))
            except Exception:
                try:
                    conn.execute(text("ALTER TABLE credit_notes ADD COLUMN invoice_date DATE NULL AFTER invoice_number"))
                except Exception:
                    pass

    except Exception as e:
        logger.error(f"Error applying schema updates: {e}")


    db = SessionLocal()
    try:
        from app.services.seed import run_seed
        run_seed(db)
        # On every server start, mark all previously "active" sessions as logged out.
        # This prevents stale "Online" entries in History when the server was restarted
        # and users didn't explicitly logout (SSE connection was cut by the restart).
        from app.models.models import UserSession
        stale = db.query(UserSession).filter(UserSession.is_active == True).all()
        for s in stale:
            s.is_active = False
            s.logout_at = datetime.now(timezone.utc)
        db.commit()
        if stale:
            logger.info(f"Cleared {len(stale)} stale session(s) from previous server run.")
    finally:
        db.close()

    # ── APScheduler: auto-purge soft-deleted records after 24 hours ────────────
    def purge_expired_deleted_records():
        """Har 1 ghante mein check karo — jis record ka permanent_delete_at past mein
        aa gaya, usse permanently delete karo (including associated files)."""
        from app.models.models import Sale, PurchaseOrder, WorkOrder, WorkOrderSale, CreditNote
        db_purge = SessionLocal()
        try:
            now_naive = datetime.utcnow()

            # Purge expired Sales
            expired_sales = db_purge.query(Sale).filter(
                Sale.is_deleted == True,
                Sale.permanent_delete_at <= now_naive,
            ).all()
            for s in expired_sales:
                logger.info(f"[AutoPurge] Permanently deleting Sale id={s.id} invoice={s.invoice_number}")
                for url_field in filter(None, [s.invoice_url, s.e_way_bill_url, s.delivery_challan_url]):
                    for url in url_field.split(";"):
                        if url and url.strip():
                            file_path = url.strip().lstrip("/")
                            if os.path.exists(file_path):
                                try:
                                    os.remove(file_path)
                                except Exception:
                                    pass
                db_purge.delete(s)
            db_purge.flush()

            # Purge expired Purchase Orders
            expired_pos = db_purge.query(PurchaseOrder).filter(
                PurchaseOrder.is_deleted == True,
                PurchaseOrder.permanent_delete_at <= now_naive,
            ).all()
            for po in expired_pos:
                logger.info(f"[AutoPurge] Permanently deleting PO id={po.id} po_number={po.po_number}")
                if po.file_url:
                    for url in po.file_url.split(";"):
                        if url and url.strip():
                            file_path = url.strip().lstrip("/")
                            if os.path.exists(file_path):
                                try:
                                    os.remove(file_path)
                                except Exception:
                                    pass
                db_purge.delete(po)
            db_purge.flush()

            # Purge expired Work Orders
            expired_wos = db_purge.query(WorkOrder).filter(
                WorkOrder.is_deleted == True,
                WorkOrder.permanent_delete_at <= now_naive,
            ).all()
            for wo in expired_wos:
                logger.info(f"[AutoPurge] Permanently deleting WO id={wo.id} wo_number={wo.wo_number}")
                if wo.file_url:
                    for url in wo.file_url.split(";"):
                        if url and url.strip():
                            file_path = url.strip().lstrip("/")
                            if os.path.exists(file_path):
                                try:
                                    os.remove(file_path)
                                except Exception:
                                    pass
                db_purge.delete(wo)
            db_purge.flush()

            # Purge expired Work Order Sales
            expired_wo_sales = db_purge.query(WorkOrderSale).filter(
                WorkOrderSale.is_deleted == True,
                WorkOrderSale.permanent_delete_at <= now_naive,
            ).all()
            for wos in expired_wo_sales:
                logger.info(f"[AutoPurge] Permanently deleting WO Sale id={wos.id} invoice={wos.invoice_number}")
                for url_field in filter(None, [wos.invoice_url, wos.e_way_bill_url, wos.delivery_challan_url]):
                    for url in url_field.split(";"):
                        if url and url.strip():
                            file_path = url.strip().lstrip("/")
                            if os.path.exists(file_path):
                                try:
                                    os.remove(file_path)
                                except Exception:
                                    pass
                db_purge.delete(wos)

            # Purge expired Credit Notes and their line items (relationship
            # cascade deletes children) after their 24-hour restore window.
            expired_credit_notes = db_purge.query(CreditNote).filter(
                CreditNote.is_deleted == True,
                CreditNote.permanent_delete_at <= now_naive,
            ).all()
            for cn in expired_credit_notes:
                logger.info(f"[AutoPurge] Permanently deleting Credit Note id={cn.id} cn={cn.cn_number}")
                db_purge.delete(cn)
            db_purge.flush()

            db_purge.commit()
            total = len(expired_sales) + len(expired_pos) + len(expired_wos) + len(expired_wo_sales)
            if total:
                logger.info(f"[AutoPurge] Purged {total} expired soft-deleted record(s).")
        except Exception as ex:
            db_purge.rollback()
            logger.error(f"[AutoPurge] Error during purge: {ex}")
        finally:
            db_purge.close()

    from apscheduler.schedulers.background import BackgroundScheduler
    _scheduler = BackgroundScheduler()
    _scheduler.add_job(purge_expired_deleted_records, "interval", hours=1, id="purge_deleted")
    _scheduler.start()
    logger.info("[AutoPurge] APScheduler started — purge job runs every hour.")

    yield
    _scheduler.shutdown(wait=False)
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

# Ensure uploads directory exists
UPLOAD_DIR = "uploads"
if not os.path.exists(UPLOAD_DIR):
    os.makedirs(UPLOAD_DIR)

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
app.include_router(work_orders.router)
app.include_router(work_order_reports.router)
app.include_router(work_order_sales.router)
app.include_router(item_master.router)
app.include_router(uom.router)
app.include_router(company_addresses.router)
app.include_router(system.router)
app.include_router(recently_deleted.router)
app.include_router(credit_notes.router)


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
