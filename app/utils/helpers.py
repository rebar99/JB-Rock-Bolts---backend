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


def recalc_po_delivered_quantities(db: Session, po) -> None:
    """Rebuild PurchaseOrder/POLineItem delivered_quantity from actual SaleItem rows.

    This always recomputes from scratch (fresh SUM over SaleItem, the source of
    truth for real dispatches) instead of accumulating with +=/-=, so the stored
    value can never drift upward from duplicate/retried calls — it is simply
    overwritten with whatever the Sales table actually contains.
    """
    from sqlalchemy import func
    from app.models.models import Sale, SaleItem

    if not po.line_items:
        total = (
            db.query(func.sum(SaleItem.quantity))
            .join(Sale, SaleItem.sale_id == Sale.id)
            .filter(Sale.po_id == po.id)
            .scalar() or 0
        )
        po.delivered_quantity = round(max(0, float(total)), 10)
        return

    po_sale_ids = [s.id for s in po.sales]
    total_all = 0.0
    for li in po.line_items:
        by_id = db.query(func.sum(SaleItem.quantity)).filter(SaleItem.line_item_id == li.id).scalar() or 0
        by_name = 0.0
        if po_sale_ids:
            by_name = db.query(func.sum(SaleItem.quantity)).filter(
                SaleItem.sale_id.in_(po_sale_ids),
                SaleItem.line_item_id.is_(None),
                SaleItem.item.ilike(li.item),
            ).scalar() or 0
        li.delivered_quantity = round(max(0, float(by_id) + float(by_name)), 10)
        total_all += li.delivered_quantity
    po.delivered_quantity = round(max(0, total_all), 10)


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
    entity_name: str = None,
    changed_fields: str = None,
    status: str = "Success",
):
    from app.models.models import SystemLog
    from app import notifications
    try:
        log_entry = SystemLog(
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            entity_name=entity_name,
            details=details,
            changed_fields=changed_fields,
            status=status,
            user=user,
        )
        db.add(log_entry)
        db.commit()
        db.refresh(log_entry)  # populate id and created_at before broadcasting
        notifications.broadcast(log_entry)
    except Exception as e:
        # Logging failures must never break the main application flow
        print(f"Failed to log activity: {e}")
        db.rollback()
