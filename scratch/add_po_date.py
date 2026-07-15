"""One-off migration: add purchase_orders.po_date and validity_date columns.

Base.metadata.create_all() (run at app startup) only creates missing tables,
it does not alter existing ones — so this needs to run once, manually,
against the existing database before the po_date/validity_date model fields
are used. (The app's startup lifespan in app/main.py now also applies this
migration automatically, so running this script by hand is optional.)

Run from the backend/ directory (module mode, so `app` resolves on sys.path):
    python -m scratch.add_po_date
"""
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, ProgrammingError

from app.database import engine

with engine.connect() as conn:
    for column, ddl in [
        ("validity_date", "ALTER TABLE purchase_orders ADD COLUMN validity_date DATETIME NULL"),
        ("po_date", "ALTER TABLE purchase_orders ADD COLUMN po_date DATETIME NULL AFTER validity_date"),
    ]:
        try:
            conn.execute(text(ddl))
            conn.commit()
            print(f"Added {column} to purchase_orders")
        except (OperationalError, ProgrammingError) as e:
            if "duplicate column" in str(e).lower():
                print(f"{column} already exists on purchase_orders — nothing to do")
            else:
                raise
