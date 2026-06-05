from datetime import datetime
from sqlalchemy.orm import Session
from app.models.models import Sale


def generate_invoice_number(db: Session) -> str:
    year = datetime.now().year
    count = db.query(Sale).filter(
        Sale.invoice_number.like(f"INV-{year}-%")
    ).count()
    return f"INV-{year}-{str(count + 1).zfill(4)}"


def compute_sale_financials(
    dispatched_qty: float,
    unit_price: float,
    gst_rate: float,
    freight: float,
) -> dict:
    subtotal = dispatched_qty * unit_price
    gst_amount = subtotal * gst_rate / 100
    grand_total = subtotal + gst_amount + freight
    return {
        "subtotal": round(subtotal, 2),
        "gst_amount": round(gst_amount, 2),
        "grand_total": round(grand_total, 2),
    }


def derive_inventory_status(quantity: int) -> str:
    from app.models.models import InventoryStatus
    if quantity <= 0:
        return InventoryStatus.OUT_OF_STOCK
    if quantity < 100:
        return InventoryStatus.LOW_STOCK
    return InventoryStatus.IN_STOCK


def log_activity(
    db: Session,
    action: str,
    entity_type: str,
    details: str,
    user: str,
    entity_id: int = None,
):
    from app.models.models import SystemLog
    try:
        log_entry = SystemLog(
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details,
            user=user
        )
        db.add(log_entry)
        db.commit()
    except Exception as e:
        # We don't want logging failures to break the main application flow
        print(f"Failed to log activity: {e}")
        db.rollback()
