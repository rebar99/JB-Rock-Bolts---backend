from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.models import Client, Project, WorkOrder
from app.utils.helpers import normalize_client_name, normalize_project_name, dedupe_names_by_normalized_key

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

WO_STATUSES = ["Pending", "In Progress", "Partial", "Completed", "Closed", "Cancelled"]
WO_PRIORITIES = ["Low", "Medium", "High", "Urgent"]


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

    # Grouped by normalized name (not a raw exact-string set()), so
    # "M/s. Afcons", "M/s Afcons Infrastructure Limited" and "afcons
    # infrastructure limited" collapse into a single dropdown entry instead
    # of each showing up as if they were different clients/projects.
    db_clients = db.query(Client.name).all()
    all_clients = dedupe_names_by_normalized_key([c[0] for c in db_clients], normalize_client_name)

    db_projects = db.query(Project.name).all()
    all_projects = dedupe_names_by_normalized_key([p[0] for p in db_projects], normalize_project_name)

    # Work Orders draw their "Name of Client" dropdown from clients that
    # actually have a Work Order on record — not the shared Clients master
    # table `all_clients` uses, which also includes clients that only ever
    # appeared on a Purchase Order. Selecting "Add New Client" still works
    # for a brand-new client with no WO yet (it sets the form field
    # directly rather than requiring the name to already be in this list).
    wo_client_rows = db.query(WorkOrder.client_name).all()
    wo_clients = dedupe_names_by_normalized_key([c[0] for c in wo_client_rows], normalize_client_name)

    return {
        "products": PRODUCTS,
        "uom_options": UOM_OPTIONS,
        "locations": LOCATIONS,
        "projects": all_projects,
        "payment_terms": PAYMENT_TERMS,
        "clients": all_clients,
        "wo_clients": wo_clients,
        "payment_statuses": ["Pending", "Partial", "Paid"],
        "delivery_statuses": ["Not Delivered", "Delivered"],
        "inventory_statuses": ["In Stock", "Low Stock", "Out of Stock"],
        "wo_statuses": WO_STATUSES,
        "wo_priorities": WO_PRIORITIES,
    }
