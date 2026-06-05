from app.database import engine
from sqlalchemy import text

with engine.connect() as conn:
    res = conn.execute(text("SHOW CREATE TABLE sales"))
    print(res.fetchone()[1])
