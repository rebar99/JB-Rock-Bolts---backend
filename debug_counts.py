from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker
from app.models.models import PurchaseOrder, Client, Sale
from app.config import settings

engine = create_engine(settings.DATABASE_URL)
Session = sessionmaker(bind=engine)
db = Session()

print("--- DATABASE CHECK ---")
all_pos = db.query(PurchaseOrder.client_name).all()
unique_pos_clients = set(r.client_name for r in all_pos)
print(f"Total PO records: {len(all_pos)}")
print(f"Unique client names in POs: {len(unique_pos_clients)}")
print(f"List of unique PO client names: {list(unique_pos_clients)}")

all_clients = db.query(Client.name).all()
unique_clients_master = set(r.name for r in all_clients)
print(f"Total Client table records: {len(all_clients)}")
print(f"Unique names in Client table: {len(unique_clients_master)}")

all_sales = db.query(Sale.client_name).all()
unique_sales_clients = set(r.client_name for r in all_sales)
print(f"Total Sale records: {len(all_sales)}")
print(f"Unique client names in Sales: {len(unique_sales_clients)}")
db.close()
