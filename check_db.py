import sys
from sqlalchemy import create_engine, text

# Add the app directory to sys.path
sys.path.append("D:\\rebar-jbrocks\\JB-Rock-Bolts---backend")

from app.config import settings

engine = create_engine(settings.DATABASE_URL)
with engine.connect() as conn:
    print("Sales Count:", conn.execute(text("SELECT COUNT(*) FROM sales")).scalar())
    print("Purchase Orders Count:", conn.execute(text("SELECT COUNT(*) FROM purchase_orders")).scalar())
    print("Clients Count:", conn.execute(text("SELECT COUNT(*) FROM clients")).scalar())
