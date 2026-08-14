"""Seed the database with initial data matching the frontend mock data."""
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.models.models import Client, Product, Record, PurchaseOrder, User, InventoryStatus, PaymentStatus, DeliveryStatus
import logging

logger = logging.getLogger(__name__)

PRODUCTS = [
    ("JB-9 RE-BAR COUPLERS", 450),
    ("JB-10 S.N. Bolts - Soil Nails - DCP Anchors", 280),
    ("JB-11 SELF Drilling Anchors (SDA)", 180),
    ("JB-12 Epoxy Coated / Zinc Coated TMT Bars", 350),
    ("JB-13 GFRP RE-BARS", 120),
    ("JB-14 Scaffolding / Tunnel Support System", 220),
    ("JB-15 Micro piling Tubes", 95),
    ("JB-16 Slope Protection Materials", 310),
    ("JB-17 Construction Engineering Materials", 400),
    ("JB-18 Gantry Fabrication / Dam Shuttering", 60),
    ("JB-19 H Type RE-BAR Couplers", 500),
]


def run_seed(db: Session):
    from app.models.models import CompanyAddress
    if db.query(CompanyAddress).count() == 0:
        logger.info("Seeding default company address...")
        default_address = CompanyAddress(
            title="RE BAR COUPLERS INDIA PRIVATE LIMITED (FY2026-27)",
            address_text="RE BAR COUPLERS INDIA PRIVATE LIMITED (FY2026-27)\nRegd. Office: 10B, Block 23, Industrial Area, Nangal Jarialan, Distt. Una, Himachal Pradesh – 177212, India\nPlant Address: VPO Palakwaha, Tehsil Haroli, Una, Himachal Pradesh – 177220\nIEC Code No.: 2216900613\nGSTIN/UIN: 02AAFCR5621L1ZG\nState: Himachal Pradesh, Code: 02\nE-Mail: accounts@jbenineeringcorporation.com",
            is_default=True
        )
        db.add(default_address)
        db.commit()

    if db.query(User).filter_by(email="admin@jbrockbolts.com").first():
        logger.info("Database already seeded with admin user. Skipping.")
        return

    logger.info("Seeding initial data (Products & Admin)...")

    for name, qty in PRODUCTS:
        if qty <= 0:
            status = InventoryStatus.OUT_OF_STOCK
        elif qty < 100:
            status = InventoryStatus.LOW_STOCK
        else:
            status = InventoryStatus.IN_STOCK
        db.add(Product(name=name, quantity=qty, status=status))
    db.flush()

    from app.routers.users import hash_password
    admin = User(
        name="Admin User",
        email="admin@jbrockbolts.com",
        hashed_password=hash_password("admin@123"),
        is_active=True,
    )
    db.add(admin)

    db.commit()
    logger.info("Seeding complete.")
