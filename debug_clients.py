from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models.models import Client
from app.config import settings

engine = create_engine(settings.DATABASE_URL)
Session = sessionmaker(bind=engine)
db = Session()

print("--- CLIENT TABLE NAMES ---")
clients = db.query(Client.name).all()
for c in clients:
    print(f"- {c.name}")
db.close()
