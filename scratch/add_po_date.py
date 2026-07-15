"""One-off migration: add purchase_orders.po_date column.

Base.metadata.create_all() (run at app startup) only creates missing tables,
it does not alter existing ones — so this needs to run once, manually,
against the existing database before the po_date model field is used.

Run from the backend/ directory (module mode, so `app` resolves on sys.path):
    python -m scratch.add_po_date
"""
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, ProgrammingError

from app.database import engine

with engine.connect() as conn:
    try:
        conn.execute(text(
            "ALTER TABLE purchase_orders ADD COLUMN po_date DATETIME NULL AFTER validity_date"
        ))
        conn.commit()
        print("Added po_date to purchase_orders")
    except (OperationalError, ProgrammingError) as e:
        if "duplicate column" in str(e).lower():
            print("po_date already exists on purchase_orders — nothing to do")
        else:
            raise
