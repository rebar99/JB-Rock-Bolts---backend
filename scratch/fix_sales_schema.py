
from sqlalchemy import create_engine, text
from app.config import settings

engine = create_engine(settings.DATABASE_URL)

with engine.begin() as conn:
    print("Fixing Sales table schema...")
    # These columns are now moved to sale_items, so we make them nullable or drop them
    # To keep compatibility with old code if any, we'll just make them nullable
    for col in ["item", "uom", "dispatched_qty", "total_qty", "previous_delivered", "unit_price", "gst_rate", "line_item_id"]:
        try:
            conn.execute(text(f"ALTER TABLE sales MODIFY COLUMN {col} VARCHAR(300) NULL"))
            print(f"Made {col} nullable")
        except Exception as e:
            # Maybe the column doesn't exist or is different type
            try:
                # Try generic modify if VARCHAR fails (e.g. for FLOAT)
                if col in ["dispatched_qty", "total_qty", "previous_delivered", "unit_price", "gst_rate"]:
                    conn.execute(text(f"ALTER TABLE sales MODIFY COLUMN {col} FLOAT NULL"))
                elif col == "line_item_id":
                    conn.execute(text(f"ALTER TABLE sales MODIFY COLUMN {col} INT NULL"))
                print(f"Made {col} nullable (float/int)")
            except Exception as e2:
                print(f"Could not modify {col}: {e2}")

    print("Schema fix complete.")
