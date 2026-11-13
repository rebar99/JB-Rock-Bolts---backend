from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
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
from app.routers import application_access
from app.routers import store_purchase

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

    from sqlalchemy import inspect, text
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

            # Safely migrate purchase_orders.freight_gst_type
            try:
                conn.execute(text("SELECT freight_gst_type FROM purchase_orders LIMIT 1"))
            except Exception:
                try:
                    conn.execute(text("ALTER TABLE purchase_orders ADD COLUMN freight_gst_type VARCHAR(20) NOT NULL DEFAULT 'Without GST'"))
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
                ("freight_gst_type", "VARCHAR(20) NOT NULL DEFAULT 'Without GST'"),
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
                ("workspace", "VARCHAR(50) NULL DEFAULT 'Marketing'"),
            ]:
                try:
                    conn.execute(text(f"SELECT {col} FROM system_logs LIMIT 1"))
                except Exception:
                    try:
                        conn.execute(text(f"ALTER TABLE system_logs ADD COLUMN {col} {dtype}"))
                    except Exception:
                        pass

            try:
                conn.execute(text("UPDATE system_logs SET workspace = 'Marketing' WHERE workspace IS NULL OR workspace = ''"))
            except Exception:
                pass

            # Existing users predate the approval workflow — grandfather them in as active
            try:
                conn.execute(text("UPDATE users SET is_active = TRUE WHERE is_active IS NULL"))
            except Exception:
                pass

            try:
                conn.execute(text("SELECT is_super_admin FROM users LIMIT 1"))
            except Exception:
                conn.execute(text("ALTER TABLE users ADD COLUMN is_super_admin BOOLEAN NOT NULL DEFAULT FALSE"))

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

            # Groups Name is intentionally repeatable. Only the pair of
            # Groups Name + Items Name identifies an inventory row.
            try:
                indexes = conn.execute(text("SHOW INDEX FROM store_purchase_items")).mappings().all()
                if any(index["Key_name"] == "ix_store_purchase_items_name" and not index["Non_unique"] for index in indexes):
                    conn.execute(text("ALTER TABLE store_purchase_items DROP INDEX ix_store_purchase_items_name"))
                    conn.execute(text("CREATE INDEX ix_store_purchase_items_name ON store_purchase_items (name)"))
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

            # Allow dedicated series (e.g. CR-01) for each sale_type (PO vs WO)
            # and ignore soft-deleted records so they don't block active numbers.
            for drop_idx in ["cn_number", "uq_credit_notes_cn_number", "ix_credit_notes_cn_number", "uq_credit_notes_type_cn"]:
                try:
                    conn.execute(text(f"ALTER TABLE credit_notes DROP INDEX {drop_idx}"))
                except Exception:
                    pass
            try:
                conn.execute(text("ALTER TABLE credit_notes ADD INDEX ix_credit_notes_cn_number (cn_number)"))
            except Exception:
                pass
            try:
                conn.execute(text("ALTER TABLE credit_notes ADD COLUMN active_cn_number VARCHAR(50) GENERATED ALWAYS AS (IF(is_deleted = 0, cn_number, NULL)) VIRTUAL"))
            except Exception:
                pass
            try:
                conn.execute(text("ALTER TABLE credit_notes ADD UNIQUE INDEX uq_credit_notes_active (sale_type, active_cn_number)"))
            except Exception:
                pass

            # Store Purchase material ledger fields. Existing item masters and
            # balances are intentionally retained; the new columns merely add
            # receipt, valuation and issue-history capability.
            for col, dtype in [
                ("vendor_name", "VARCHAR(200) NULL"), ("reference_no", "VARCHAR(100) NULL"),
                ("receipt_date", "DATE NULL"), ("stock_item", "VARCHAR(150) NULL"),
                ("uom", "VARCHAR(50) NOT NULL DEFAULT 'Nos'"), ("location", "VARCHAR(150) NULL"),
                ("required_for", "VARCHAR(200) NULL"), ("rate", "FLOAT NOT NULL DEFAULT 0"),
                ("previous_quantity", "FLOAT NOT NULL DEFAULT 0"),
                ("current_month_quantity", "FLOAT NOT NULL DEFAULT 0"),
                ("total_received_quantity", "FLOAT NOT NULL DEFAULT 0"),
                ("issued_quantity", "FLOAT NOT NULL DEFAULT 0"), ("status", "VARCHAR(30) NOT NULL DEFAULT 'In Stock'"), ("out_of_stock_limit", "FLOAT NOT NULL DEFAULT 0"),
            ]:
                try:
                    conn.execute(text(f"SELECT {col} FROM store_purchase_items LIMIT 1"))
                except Exception:
                    try:
                        conn.execute(text(f"ALTER TABLE store_purchase_items ADD COLUMN {col} {dtype}"))
                    except Exception:
                        pass

            try:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS store_purchase_stock_transactions (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        item_id INT NOT NULL, transaction_type VARCHAR(20) NOT NULL,
                        quantity FLOAT NOT NULL, rate FLOAT NOT NULL DEFAULT 0, amount FLOAT NOT NULL DEFAULT 0,
                        transaction_date DATE NOT NULL, vendor_name VARCHAR(200) NULL, reference_no VARCHAR(100) NULL,
                        required_for VARCHAR(200) NULL, issued_to VARCHAR(200) NULL, location VARCHAR(150) NULL,
                        remarks TEXT NULL, created_at DATETIME DEFAULT CURRENT_TIMESTAMP, created_by VARCHAR(100) NULL,
                        INDEX ix_store_purchase_stock_transactions_item_id (item_id),
                        FOREIGN KEY (item_id) REFERENCES store_purchase_items(id)
                    )
                """))
            except Exception:
                pass

            try:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS store_purchase_stock_transfers (
                        id INT AUTO_INCREMENT PRIMARY KEY, item_id INT NOT NULL, quantity FLOAT NOT NULL,
                        from_location VARCHAR(150) NOT NULL, to_location VARCHAR(150) NOT NULL,
                        transfer_date DATE NOT NULL, required_for VARCHAR(200) NULL, remarks TEXT NULL,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP, created_by VARCHAR(100) NULL,
                        INDEX ix_store_purchase_stock_transfers_item_id (item_id),
                        FOREIGN KEY (item_id) REFERENCES store_purchase_items(id)
                    )
                """))
            except Exception:
                pass

            try:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS store_purchase_stock_items (
                        id INT AUTO_INCREMENT PRIMARY KEY, material_id INT NOT NULL,
                        name VARCHAR(150) NOT NULL, uom VARCHAR(50) NOT NULL DEFAULT 'Nos',
                        location VARCHAR(150) NULL, vendor_name VARCHAR(200) NULL, rate FLOAT NOT NULL DEFAULT 0,
                        total_received_quantity FLOAT NOT NULL DEFAULT 0, issued_quantity FLOAT NOT NULL DEFAULT 0,
                        available_quantity FLOAT NOT NULL DEFAULT 0, created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        UNIQUE KEY uq_store_material_stock_item (material_id, name),
                        INDEX ix_store_purchase_stock_items_material_id (material_id),
                        FOREIGN KEY (material_id) REFERENCES store_purchase_items(id)
                    )
                """))
            except Exception:
                pass

            # Preserve existing Material Received records while adding the
            # separately recorded freight charge used in receipt totals.
            try:
                conn.execute(text("SELECT freight_charge FROM store_purchase_material_receipts LIMIT 1"))
            except Exception:
                try:
                    conn.execute(text("ALTER TABLE store_purchase_material_receipts ADD COLUMN freight_charge FLOAT NOT NULL DEFAULT 0"))
                except Exception:
                    pass

            try:
                conn.execute(text("SELECT po_type FROM store_purchase_orders LIMIT 1"))
            except Exception:
                try:
                    conn.execute(text("ALTER TABLE store_purchase_orders ADD COLUMN po_type VARCHAR(30) NOT NULL DEFAULT 'Regular Vendor PO'"))
                except Exception:
                    pass

            try:
                conn.execute(text("SELECT receipt_line_id FROM store_purchase_stock_transactions LIMIT 1"))
            except Exception:
                try:
                    conn.execute(text("ALTER TABLE store_purchase_stock_transactions ADD COLUMN receipt_line_id INT NULL UNIQUE"))
                except Exception:
                    pass

            # Add updated_at / updated_by tracking to material receipts
            for col, dtype in [("updated_at", "DATETIME NULL"), ("updated_by", "VARCHAR(100) NULL")]:
                try:
                    conn.execute(text(f"SELECT {col} FROM store_purchase_material_receipts LIMIT 1"))
                except Exception:
                    try:
                        conn.execute(text(f"ALTER TABLE store_purchase_material_receipts ADD COLUMN {col} {dtype}"))
                    except Exception:
                        pass

            # Add payment tracking columns to material receipts
            for col, dtype in [
                ("payment_status", "VARCHAR(30) NOT NULL DEFAULT 'Unpaid'"),
                ("amount_paid",    "FLOAT NULL DEFAULT 0"),
                ("payment_date",   "DATE NULL"),
                ("payment_reference", "VARCHAR(200) NULL"),
            ]:
                try:
                    conn.execute(text(f"SELECT {col} FROM store_purchase_material_receipts LIMIT 1"))
                except Exception:
                    try:
                        conn.execute(text(f"ALTER TABLE store_purchase_material_receipts ADD COLUMN {col} {dtype}"))
                    except Exception:
                        pass

            # Add remark_1, remark_2, freight_charges and freight_gst_type to store_purchase_order_details
            for col, dtype in [("remark_1", "TEXT NULL"), ("remark_2", "TEXT NULL"), ("freight_charges", "FLOAT NOT NULL DEFAULT 0"), ("freight_gst_type", "VARCHAR(20) NOT NULL DEFAULT 'With GST'")]:
                try:
                    conn.execute(text(f"SELECT {col} FROM store_purchase_order_details LIMIT 1"))
                except Exception:
                    try:
                        conn.execute(text(f"ALTER TABLE store_purchase_order_details ADD COLUMN {col} {dtype}"))
                    except Exception:
                        pass

            # Soft-delete columns for store_purchase_orders
            for col, dtype in [
                ("is_deleted", "BOOLEAN NOT NULL DEFAULT FALSE"),
                ("deleted_at", "DATETIME NULL"),
                ("deleted_by", "VARCHAR(100) NULL"),
                ("permanent_delete_at", "DATETIME NULL"),
            ]:
                try:
                    conn.execute(text(f"SELECT {col} FROM store_purchase_orders LIMIT 1"))
                except Exception:
                    try:
                        conn.execute(text(f"ALTER TABLE store_purchase_orders ADD COLUMN {col} {dtype}"))
                    except Exception:
                        pass

            # Soft-delete columns and payment_due_date for store_purchase_material_receipts
            for col, dtype in [
                ("is_deleted", "BOOLEAN NOT NULL DEFAULT FALSE"),
                ("deleted_at", "DATETIME NULL"),
                ("deleted_by", "VARCHAR(100) NULL"),
                ("permanent_delete_at", "DATETIME NULL"),
                ("payment_due_date", "DATE NULL"),
            ]:
                try:
                    conn.execute(text(f"SELECT {col} FROM store_purchase_material_receipts LIMIT 1"))
                except Exception:
                    try:
                        conn.execute(text(f"ALTER TABLE store_purchase_material_receipts ADD COLUMN {col} {dtype}"))
                    except Exception:
                        pass

            # ── PO Approval Level signature columns ─────────────────────────────
            for col, dtype in [
                ("checked_by_user_id",       "INT NULL"),
                ("checked_by_signature_url", "VARCHAR(500) NULL"),
                ("approved_by_user_id",      "INT NULL"),
                ("approved_by_signature_url","VARCHAR(500) NULL"),
            ]:
                try:
                    conn.execute(text(f"SELECT {col} FROM po_approval_levels LIMIT 1"))
                except Exception:
                    try:
                        conn.execute(text(f"ALTER TABLE po_approval_levels ADD COLUMN {col} {dtype}"))
                        logger.info(f"[Migration] Added po_approval_levels.{col}")
                    except Exception as _e:
                        logger.warning(f"[Migration] po_approval_levels.{col}: {_e}")

            # ── PO approval audit snapshots ────────────────────────────────────
            for col, dtype in [("created_by_user_id", "INT NULL"), ("approval_cycle", "INT NOT NULL DEFAULT 1")]:
                try:
                    conn.execute(text(f"SELECT {col} FROM store_purchase_orders LIMIT 1"))
                except Exception:
                    try:
                        conn.execute(text(f"ALTER TABLE store_purchase_orders ADD COLUMN {col} {dtype}"))
                    except Exception as _e:
                        logger.warning(f"[Migration] store_purchase_orders.{col}: {_e}")
            for col, dtype in [("approver_name", "VARCHAR(100) NULL"), ("remarks", "TEXT NULL"), ("workflow_role", "VARCHAR(30) NOT NULL DEFAULT 'approver'"), ("signature_url", "VARCHAR(500) NULL"), ("next_approver_id", "INT NULL"), ("next_approver_name", "VARCHAR(100) NULL"), ("next_signature_url", "VARCHAR(500) NULL"), ("approval_cycle", "INT NOT NULL DEFAULT 1")]:
                try:
                    conn.execute(text(f"SELECT {col} FROM store_purchase_order_approvals LIMIT 1"))
                except Exception:
                    try:
                        conn.execute(text(f"ALTER TABLE store_purchase_order_approvals ADD COLUMN {col} {dtype}"))
                        logger.info(f"[Migration] Added store_purchase_order_approvals.{col}")
                    except Exception as _e:
                        logger.warning(f"[Migration] store_purchase_order_approvals.{col}: {_e}")

            # ── po_prepared_by_config (legacy singleton) ─────────────────────────
            try:
                conn.execute(text("SELECT id FROM po_prepared_by_config LIMIT 1"))
            except Exception:
                try:
                    conn.execute(text("""
                        CREATE TABLE IF NOT EXISTS po_prepared_by_config (
                            id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                            user_id INT NULL,
                            user_name VARCHAR(100) NULL,
                            signature_url VARCHAR(500) NULL,
                            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                            updated_by VARCHAR(100) NULL,
                            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
                        ) CHARACTER SET utf8mb4
                    """))
                    logger.info("[Migration] Created po_prepared_by_config table")
                except Exception as _e:
                    logger.warning(f"[Migration] po_prepared_by_config: {_e}")

            # ── po_prepared_by_signatures (multi-user prepared-by) ───────────────
            try:
                conn.execute(text("SELECT id FROM po_prepared_by_signatures LIMIT 1"))
            except Exception:
                try:
                    conn.execute(text("""
                        CREATE TABLE IF NOT EXISTS po_prepared_by_signatures (
                            id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                            user_id INT NOT NULL UNIQUE,
                            user_name VARCHAR(100) NULL,
                            signature_url VARCHAR(500) NULL,
                            display_order INT NOT NULL DEFAULT 0,
                            added_by VARCHAR(100) NULL,
                            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                        ) CHARACTER SET utf8mb4
                    """))
                    logger.info("[Migration] Created po_prepared_by_signatures table")
                except Exception as _e:
                    logger.warning(f"[Migration] po_prepared_by_signatures: {_e}")

    except Exception as e:
        logger.error(f"Error applying schema updates: {e}")

    # These fields are required by the Store PO ORM query. Some hosts suppress
    # failures from the general best-effort migration block above, so verify
    # them independently before serving requests.
    try:
        with engine.begin() as conn:
            columns = {column["name"] for column in inspect(conn).get_columns("store_purchase_order_details")}
            if "freight_charges" not in columns:
                conn.execute(text(
                    "ALTER TABLE store_purchase_order_details "
                    "ADD COLUMN freight_charges FLOAT NOT NULL DEFAULT 0"
                ))
                logger.info("[Migration] Added store_purchase_order_details.freight_charges")
            if "freight_gst_type" not in columns:
                conn.execute(text(
                    "ALTER TABLE store_purchase_order_details "
                    "ADD COLUMN freight_gst_type VARCHAR(20) NOT NULL DEFAULT 'With GST'"
                ))
                logger.info("[Migration] Added store_purchase_order_details.freight_gst_type")
    except Exception:
        logger.exception("Could not ensure store PO freight GST schema; API startup aborted.")
        raise


    db = SessionLocal()
    try:
        from app.models.models import Application, ApplicationAccess, User
        from app.config import settings
        for name, code in [("Marketing", "marketing"), ("Store Purchase", "store_purchase")]:
            if not db.query(Application).filter_by(code=code).first():
                db.add(Application(name=name, code=code))
        db.commit()
        marketing = db.query(Application).filter_by(code="marketing").one()
        legacy_admins = [email.strip().lower() for email in settings.ADMIN_EMAIL.split(",") if email.strip()]
        super_admins = [email.strip().lower() for email in settings.SUPER_ADMIN_EMAIL.split(",") if email.strip()]
        for user in db.query(User).all():
            if user.email.lower() in legacy_admins and not db.query(ApplicationAccess).filter_by(user_id=user.id, application_id=marketing.id).first():
                db.add(ApplicationAccess(user_id=user.id, application_id=marketing.id, role="admin"))
            if user.email.lower() in super_admins:
                user.is_super_admin = True
        db.commit()
        from app.services.seed import run_seed
        run_seed(db)

        # Recalculate all PO and WO quantities to ensure database is perfectly synced
        try:
            from app.models.models import PurchaseOrder, WorkOrder
            from app.utils.helpers import recalc_po_delivered_quantities, recalc_wo_completed_quantities
            for po_rec in db.query(PurchaseOrder).all():
                recalc_po_delivered_quantities(db, po_rec)
            for wo_rec in db.query(WorkOrder).all():
                recalc_wo_completed_quantities(db, wo_rec)
            db.commit()
            logger.info('[Startup] Recalculated all PO and WO delivered quantities.')
        except Exception as _recalc_err:
            logger.warning(f'[Startup] Error recalculating quantities: {_recalc_err}')
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
        from app.models.models import Sale, PurchaseOrder, WorkOrder, WorkOrderSale, CreditNote, StorePurchaseOrder, StorePurchaseMaterialReceipt
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

            # Purge expired Store Purchase Orders
            expired_store_pos = db_purge.query(StorePurchaseOrder).filter(
                StorePurchaseOrder.is_deleted == True,
                StorePurchaseOrder.permanent_delete_at <= now_naive,
            ).all()
            for spo in expired_store_pos:
                logger.info(f"[AutoPurge] Permanently deleting Store PO id={spo.id} order={spo.order_number}")
                db_purge.delete(spo)
            db_purge.flush()

            # Purge expired Material Receipts
            expired_receipts = db_purge.query(StorePurchaseMaterialReceipt).filter(
                StorePurchaseMaterialReceipt.is_deleted == True,
                StorePurchaseMaterialReceipt.permanent_delete_at <= now_naive,
            ).all()
            for rcpt in expired_receipts:
                logger.info(f"[AutoPurge] Permanently deleting Material Receipt id={rcpt.id} invoice={rcpt.invoice_number}")
                db_purge.delete(rcpt)
            db_purge.flush()

            db_purge.commit()
            total = len(expired_sales) + len(expired_pos) + len(expired_wos) + len(expired_wo_sales) + len(expired_credit_notes) + len(expired_store_pos) + len(expired_receipts)
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
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


@app.middleware("http")
async def enforce_marketing_application_access(request, call_next):
    """Backend enforcement for the existing Marketing API.

    The Store Purchase router performs its own store_purchase checks.  Keeping
    this boundary here means a button hidden in React can never become the
    security control for the legacy Marketing endpoints.
    """
    path = request.url.path
    query_string = request.url.query.lower()
    is_store_request = (
        path.startswith("/api/store-purchase")
        or (path.startswith("/api/uom") and "type=store" in query_string)
        or (path.startswith("/api/item-master") and "type=store" in query_string)
    )
    is_shared_request = (
        path.startswith("/api/logs")
        or path.startswith("/api/company-addresses")
        or path.startswith("/api/recently-deleted")
    )
    public_or_non_marketing = (
        not path.startswith("/api/")
        or path.startswith("/api/uploads")
        or path.startswith("/api/users/")
        or path == "/api/users"
        or path.startswith("/api/application-access")
        or is_store_request
        or request.method == "OPTIONS"
    )
    if public_or_non_marketing:
        return await call_next(request)

    from app.utils.auth import get_user_id_from_token, _decode_raw_token, require_application_access
    from app.models.models import User
    from fastapi.responses import JSONResponse
    def _cors_response(status_code: int, content: dict) -> JSONResponse:
        resp = JSONResponse(status_code=status_code, content=content)
        origin = request.headers.get("origin")
        if origin:
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Access-Control-Allow-Credentials"] = "true"
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
            resp.headers["Access-Control-Allow-Headers"] = "*"
        return resp

    authorization = request.headers.get("Authorization", "")
    user_id = get_user_id_from_token(authorization) or _decode_raw_token(request.query_params.get("token", ""))
    if not user_id:
        return _cors_response(401, {"detail": "Invalid or expired token."})
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if not user or not user.is_active:
            return _cors_response(401, {"detail": "User not found or inactive."})
        if not is_shared_request:
            try:
                require_application_access(user, db, "marketing")
            except Exception as exc:
                return _cors_response(403, {"detail": getattr(exc, "detail", "No Marketing access.")})
    finally:
        db.close()
    return await call_next(request)

# Ensure uploads directory exists (use absolute path to be independent of CWD)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
if not os.path.exists(UPLOAD_DIR):
    os.makedirs(UPLOAD_DIR, exist_ok=True)

# Smart file serving route with automatic fallback to alternative server folders
@app.get("/api/uploads/{file_name:path}")
@app.get("/uploads/{file_name:path}")
async def serve_uploaded_file(file_name: str):
    clean_name = file_name.lstrip("/").replace("\\", "/")
    # 1. Primary UPLOAD_DIR
    target = os.path.abspath(os.path.join(UPLOAD_DIR, clean_name))
    if os.path.isfile(target):
        return FileResponse(target)

    # 2. Check candidate fallback directories where uploads may have landed earlier
    candidate_dirs = [
        UPLOAD_DIR,
        os.path.join(os.getcwd(), "uploads"),
        os.path.join(BASE_DIR, "app", "uploads"),
        os.path.join(os.path.dirname(BASE_DIR), "uploads"),
        "/var/www/uploads",
        "/var/www/marketing/uploads",
        "/var/www/marketing/JB-Rock-Bolts---backend/uploads",
        "/root/uploads",
    ]
    for cdir in candidate_dirs:
        try:
            cand = os.path.abspath(os.path.join(cdir, clean_name))
            if os.path.isfile(cand):
                try:
                    import shutil
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    shutil.copy2(cand, target)
                except Exception:
                    pass
                return FileResponse(cand)
        except Exception:
            continue

    # 3. Fast Linux system-wide find across root / common directories
    import subprocess
    base_name = os.path.basename(clean_name)
    prefix_query = base_name.split(".")[0]
    for sdir in ["/var/www", "/home", "/root", "/tmp", "/var"]:
        if not os.path.isdir(sdir):
            continue
        try:
            res = subprocess.run(
                ["find", sdir, "-name", f"*{prefix_query}*", "-type", "f"],
                capture_output=True,
                text=True,
                timeout=4
            )
            if res.returncode == 0 and res.stdout.strip():
                matches = [p.strip() for p in res.stdout.strip().splitlines() if p.strip()]
                for match in matches:
                    if os.path.isfile(match):
                        try:
                            import shutil
                            os.makedirs(os.path.dirname(target), exist_ok=True)
                            shutil.copy2(match, target)
                        except Exception:
                            pass
                        return FileResponse(match)
        except Exception:
            continue

    # 4. Search directory tree by filename
    try:
        search_roots = [BASE_DIR, os.path.dirname(BASE_DIR), os.getcwd()]
        for sroot in search_roots:
            if not os.path.isdir(sroot):
                continue
            for root, _, files in os.walk(sroot):
                if base_name in files:
                    found = os.path.join(root, base_name)
                    if os.path.isfile(found):
                        try:
                            import shutil
                            os.makedirs(os.path.dirname(target), exist_ok=True)
                            shutil.copy2(found, target)
                        except Exception:
                            pass
                        return FileResponse(found)
    except Exception:
        pass

    # 5. Dynamic Fallback: If physical file was omitted in backup migration,
    # generate the official, verified document dynamically from the database
    # so the user can immediately view and print it without errors!
    try:
        from app.database import SessionLocal
        from app.models.models import Sale, WorkOrderSale
        from app.routers.documents import generate_delivery_challan_html, get_invoice_document, get_wo_invoice_document
        from fastapi.responses import HTMLResponse

        fallback_db = SessionLocal()
        try:
            # Check Sale records
            matched_sale = fallback_db.query(Sale).filter(
                (Sale.delivery_challan_url.like(f"%{clean_name}%")) |
                (Sale.invoice_url.like(f"%{clean_name}%")) |
                (Sale.e_way_bill_url.like(f"%{clean_name}%"))
            ).first()

            if matched_sale:
                if matched_sale.delivery_challan_url and clean_name in matched_sale.delivery_challan_url:
                    challan_html = generate_delivery_challan_html(matched_sale, is_wo=False)
                    return HTMLResponse(content=challan_html, status_code=200)
                if matched_sale.invoice_url and clean_name in matched_sale.invoice_url:
                    return get_invoice_document(matched_sale.id, db=fallback_db)

            # Check WorkOrderSale records
            matched_wo = fallback_db.query(WorkOrderSale).filter(
                (WorkOrderSale.delivery_challan_url.like(f"%{clean_name}%")) |
                (WorkOrderSale.invoice_url.like(f"%{clean_name}%")) |
                (WorkOrderSale.e_way_bill_url.like(f"%{clean_name}%"))
            ).first()

            if matched_wo:
                if matched_wo.delivery_challan_url and clean_name in matched_wo.delivery_challan_url:
                    challan_html = generate_delivery_challan_html(matched_wo, is_wo=True)
                    return HTMLResponse(content=challan_html, status_code=200)
                if matched_wo.invoice_url and clean_name in matched_wo.invoice_url:
                    return get_wo_invoice_document(matched_wo.id, db=fallback_db)
        finally:
            fallback_db.close()
    except Exception as exc:
        logging.getLogger(__name__).warning(f"Dynamic fallback document generation failed: {exc}")

    raise HTTPException(
        status_code=404,
        detail="File not found on server. Yeh file server disk par maujood nahi hai. Kripya is delivery challan ko dubara upload karein."
    )

@app.get("/api/uploads-diagnostic")
async def uploads_diagnostic():
    import getpass
    cwd = os.getcwd()
    user = getpass.getuser()
    exists = os.path.isdir(UPLOAD_DIR)
    files_in_upload_dir = os.listdir(UPLOAD_DIR) if exists else []

    discovered = []
    for d in ["/var/www", "/home", "/root", "/tmp", cwd, BASE_DIR]:
        if os.path.isdir(d):
            try:
                for root, dirs, _ in os.walk(d):
                    if "uploads" in dirs:
                        p = os.path.join(root, "uploads")
                        if p not in [x["path"] for x in discovered]:
                            count = len(os.listdir(p)) if os.path.isdir(p) else 0
                            sample = os.listdir(p)[:5] if os.path.isdir(p) else []
                            discovered.append({"path": p, "files_count": count, "sample": sample})
            except Exception:
                pass

    return {
        "status": "ok",
        "user": user,
        "cwd": cwd,
        "base_dir": BASE_DIR,
        "upload_dir": UPLOAD_DIR,
        "upload_dir_exists": exists,
        "total_files_in_upload_dir": len(files_in_upload_dir),
        "sample_files": files_in_upload_dir[:15],
        "discovered_upload_folders": discovered,
    }

app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
app.mount("/api/uploads", StaticFiles(directory=UPLOAD_DIR), name="api_uploads")

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
app.include_router(application_access.router)
app.include_router(store_purchase.router)


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
