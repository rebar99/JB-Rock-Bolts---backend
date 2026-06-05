from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models.models import PurchaseOrder
from app.config import settings

engine = create_engine(settings.DATABASE_URL)
Session = sessionmaker(bind=engine)
db = Session()

all_po_clients = db.query(PurchaseOrder.client_name).all()
normalized_names = set()
for row in all_po_clients:
    name = row.client_name.upper()
    name = name.replace("M/S.", "").replace("M/S", "").replace("LIMITED", "").replace("LTD.", "").replace("LTD", "")
    name = name.replace("PROJECTS", "").replace("PRODUCT", "")
    name = name.replace(".", "").replace(",", "").replace(" ", "").strip()
    if name:
        normalized_names.add(name)

print(f"Updated Smart Normalized Client Count: {len(normalized_names)}")
print(f"Final Normalized Names: {sorted(list(normalized_names))}")
db.close()
