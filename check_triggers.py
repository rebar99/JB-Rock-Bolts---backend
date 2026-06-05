from app.database import engine
from sqlalchemy import text

with engine.connect() as conn:
    res = conn.execute(text("SHOW TRIGGERS"))
    rows = res.fetchall()
    if not rows:
        print("No triggers found.")
    for row in rows:
        print(row)
