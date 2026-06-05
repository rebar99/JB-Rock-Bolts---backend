from app.database import engine
from sqlalchemy import text

with engine.connect() as conn:
    print("Updating Sale 6 to 999...")
    res = conn.execute(text("UPDATE sales SET grand_total = 999 WHERE id = 6"))
    conn.commit()
    print(f"Rows affected: {res.rowcount}")
    
    res = conn.execute(text("SELECT grand_total FROM sales WHERE id = 6"))
    print(f"New Grand Total: {res.fetchone()[0]}")
