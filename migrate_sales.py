import sys
from sqlalchemy import text
from app.database import engine

print("Starting database migration for sales table...")
try:
    with engine.connect() as conn:
        # Check existing columns
        result = conn.execute(text("SHOW COLUMNS FROM sales"))
        existing_columns = [row[0] for row in result.fetchall()]
        
        queries = []
        if 'delivery_status' not in existing_columns:
            queries.append("ALTER TABLE sales ADD COLUMN delivery_status VARCHAR(50) NOT NULL DEFAULT 'Pending'")
        if 'delivery_challan_url' not in existing_columns:
            queries.append("ALTER TABLE sales ADD COLUMN delivery_challan_url VARCHAR(500)")

        for q in queries:
            print(f"Executing: {q}")
            conn.execute(text(q))
        
        conn.commit()
        print("Migration completed successfully!")
except Exception as e:
    print(f"Error during migration: {e}")
    sys.exit(1)
