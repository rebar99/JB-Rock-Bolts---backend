
from sqlalchemy import create_engine, inspect
from app.config import settings

engine = create_engine(settings.DATABASE_URL)
inspector = inspect(engine)

columns = inspector.get_columns("sales")
for c in columns:
    print(f"{c['name']}: {c['type']}, Nullable: {c['nullable']}, Default: {c['default']}")
