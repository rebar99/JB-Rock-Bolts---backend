from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.models import Client, Project

router = APIRouter(prefix="/api/constants", tags=["Constants"])

PRODUCTS = [
    "JB-9 RE-BAR COUPLERS",
    "JB-10 S.N. Bolts - Soil Nails - DCP Anchors",
    "JB-11 SELF Drilling Anchors (SDA)",
    "JB-12 Epoxy Coated / Zinc Coated TMT Bars",
    "JB-13 GFRP RE-BARS",
    "JB-14 Scaffolding / Tunnel Support System",
    "JB-15 Micro piling Tubes",
    "JB-16 Slope Protection Materials",
    "JB-17 Construction Engineering Materials",
    "JB-18 Gantry Fabrication / Dam Shuttering",
    "JB-19 H Type RE-BAR Couplers",
]

UOM_OPTIONS = ["Nos", "MT", "Kg", "Ton", "Set", "Meter", "Sqm", "Cum", "Ltr", "Box", "Unit", "ZNS", "PC"]

LOCATIONS = [
    "Delhi", "Mumbai", "Pune", "Bengaluru", "Chennai",
    "Hyderabad", "Ahmedabad", "Kolkata", "Jaipur", "Lucknow", "Indore", "Surat",
]

PAYMENT_TERMS = [
    "30 Days Credit",
    "45 Days Credit",
    "60 Days Credit",
    "Advance 50%",
    "100% Advance",
    "Against Delivery",
]


from sqlalchemy import text

@router.get("")
def get_constants(db: Session = Depends(get_db)):
    # Safely migrate purchase_orders.client_id
    try:
        db.execute(text("SELECT client_id FROM purchase_orders LIMIT 1"))
    except Exception:
        db.rollback()
        try:
            db.execute(text("ALTER TABLE purchase_orders ADD COLUMN client_id INT NULL"))
            db.commit()
        except Exception:
            db.rollback()

    # Safely migrate purchase_orders.project_id
    try:
        db.execute(text("SELECT project_id FROM purchase_orders LIMIT 1"))
    except Exception:
        db.rollback()
        try:
            db.execute(text("ALTER TABLE purchase_orders ADD COLUMN project_id INT NULL"))
            db.commit()
        except Exception:
            db.rollback()
            
    # Safely add constraints to purchase_orders
    try:
        db.execute(text("ALTER TABLE purchase_orders ADD CONSTRAINT fk_po_client FOREIGN KEY (client_id) REFERENCES clients(id)"))
        db.commit()
    except Exception:
        db.rollback()
        
    try:
        db.execute(text("ALTER TABLE purchase_orders ADD CONSTRAINT fk_po_project FOREIGN KEY (project_id) REFERENCES projects(id)"))
        db.commit()
    except Exception:
        db.rollback()

    # Safely migrate records.client_id
    try:
        db.execute(text("SELECT client_id FROM records LIMIT 1"))
    except Exception:
        db.rollback()
        try:
            db.execute(text("ALTER TABLE records ADD COLUMN client_id INT NULL"))
            db.commit()
        except Exception:
            db.rollback()
            
    # Safely add constraints to records
    try:
        db.execute(text("ALTER TABLE records ADD CONSTRAINT fk_rec_client FOREIGN KEY (client_id) REFERENCES clients(id)"))
        db.commit()
    except Exception:
        db.rollback()

    db_clients = db.query(Client.name).all()
    all_clients = sorted(list(set([c[0] for c in db_clients])))

    db_projects = db.query(Project.name).all()
    all_projects = sorted(list(set([p[0] for p in db_projects])))

    return {
        "products": PRODUCTS,
        "uom_options": UOM_OPTIONS,
        "locations": LOCATIONS,
        "projects": all_projects,
        "payment_terms": PAYMENT_TERMS,
        "clients": all_clients,
        "payment_statuses": ["Pending", "Partial", "Paid"],
        "delivery_statuses": ["Not Delivered", "Delivered"],
        "inventory_statuses": ["In Stock", "Low Stock", "Out of Stock"],
    }
