from dotenv import load_dotenv
load_dotenv(r"D:\rebar-jbrocks\JB-Rock-Bolts---backend\.env")
from app.database import engine
from sqlalchemy import text

with engine.connect() as conn:
    conn.execute(text("UPDATE hr_salary_config SET config_value = '0.75' WHERE config_key = 'esi_percentage'"))
    conn.commit()
    rows = conn.execute(text("SELECT config_key, config_value FROM hr_salary_config")).fetchall()
    for r in rows:
        print(r[0], "=>", r[1])
