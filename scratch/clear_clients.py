import os
import sys
from sqlalchemy import text

# Add the current directory to sys.path to find 'app'
sys.path.append(os.getcwd())

try:
    from app.database import SessionLocal
    
    db = SessionLocal()
    
    # Disable foreign key checks for a clean wipe
    db.execute(text("SET FOREIGN_KEY_CHECKS = 0;"))
    
    # Clear all related tables
    db.execute(text("TRUNCATE TABLE projects;"))
    db.execute(text("TRUNCATE TABLE clients;"))
    db.execute(text("TRUNCATE TABLE records;"))
    
    # Optional: Clear POs and Sales if not already cleared
    db.execute(text("TRUNCATE TABLE po_line_items;"))
    db.execute(text("TRUNCATE TABLE sale_activities;"))
    db.execute(text("TRUNCATE TABLE sales;"))
    db.execute(text("TRUNCATE TABLE purchase_orders;"))
    
    # Re-enable foreign key checks
    db.execute(text("SET FOREIGN_KEY_CHECKS = 1;"))
    
    db.commit()
    db.close()
    print("Successfully cleared all Clients, Projects, and related records.")
except Exception as e:
    print(f"Error: {e}")
