
from sqlalchemy import create_engine, inspect, text
from app.config import settings

engine = create_engine(settings.DATABASE_URL)
inspector = inspect(engine)

print("--- SALES TABLE COLUMNS ---")
columns = inspector.get_columns("sales")
for c in columns:
    print(f"{c['name']}: {c['type']}")

print("\n--- TABLES IN DB ---")
print(inspector.get_table_names())
