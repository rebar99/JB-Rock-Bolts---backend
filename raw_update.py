from app.database import engine
from sqlalchemy import text

with engine.connect() as conn:
    print("Updating Sale 6...")
    res = conn.execute(text("UPDATE sales SET grand_total = 10252548.0, gst_amount = 1563948.0 WHERE id = 6"))
    conn.commit()
    print(f"Rows affected: {res.rowcount}")
    
    res = conn.execute(text("SELECT grand_total FROM sales WHERE id = 6"))
    print(f"New Grand Total: {res.fetchone()[0]}")
