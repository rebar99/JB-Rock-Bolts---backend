import logging
logging.disable(logging.CRITICAL)
from app.database import engine
from sqlalchemy import text

with engine.connect() as conn:
    try:
        conn.execute(text("ALTER TABLE system_logs ADD COLUMN workspace VARCHAR(50) DEFAULT 'Marketing'"))
        conn.commit()
        print("Column 'workspace' added successfully to system_logs")
    except Exception as e:
        print(f"Note: {e}")
