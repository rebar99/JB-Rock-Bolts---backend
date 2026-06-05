import urllib.request
import json
import pprint

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from app.config import settings

engine = create_engine(settings.DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
db = SessionLocal()

result = db.execute(text("SELECT id, item, quantity, delivered_quantity FROM po_line_items WHERE po_id=3")).fetchall()
for r in result:
    print(r)
