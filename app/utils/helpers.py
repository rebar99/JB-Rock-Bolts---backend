from datetime import datetime
from sqlalchemy.orm import Session
from app.models.models import Sale


def normalize_client_name(name: str) -> str:
    """Collapse a free-text client name down to a comparable key, so
    'M/s. Afcons', 'M/S AFCONS', and 'afcons' are recognized as the same
    client. Case, leading/trailing/internal spacing, punctuation, and common
    legal-entity suffixes (Ltd, Pvt, Limited, etc.) are all normalized away.
    This is the single implementation used everywhere a unique-client count
    is needed, so every such count agrees by construction.
    """
    if not name:
        return ""
    n = name.upper()
    n = n.replace("M/S.", "").replace("M/S", "").replace("LIMITED", "").replace("LTD.", "").replace("LTD", "")
    n = n.replace("PRIVATE", "").replace("PVT.", "").replace("PVT", "")
    n = n.replace("PROJECTS", "").replace("PRODUCT", "")
    n = n.replace(".", "").replace(",", "").replace(" ", "").strip()
    return n


def generate_invoice_number(db: Session) -> str:
    year = datetime.now().year
    count = db.query(Sale).filter(
        Sale.invoice_number.like(f"INV-{year}-%")
    ).count()
    return f"INV-{year}-{str(count + 1).zfill(4)}"


def compute_line_taxable_and_gst(quantity: float, unit_price: float, gst_rate: float) -> tuple:
    """Recompute Taxable Amount and GST Amount for a single dispatch line item,
    directly from the raw values entered by the user: quantity, unit price, and
    GST %. This never reads a stored subtotal/gst_amount column — it is always
    derived fresh, so it can't drift, be cached, or be reused across calls.

    GST Amount = Taxable Amount x GST% / 100  (Taxable Amount = quantity x unit_price)
    """
    taxable_amount = float(quantity or 0) * float(unit_price or 0)
    gst_amount = taxable_amount * float(gst_rate or 0) / 100
    return round(taxable_amount, 2), round(gst_amount, 2)


def compute_sale_taxable_and_gst(items) -> tuple:
    """Recompute a Sale's Taxable Amount and GST Amount by independently
    calculating every one of its line items (via compute_line_taxable_and_gst)
    and summing the results. Every Sales record — and every line within it —
    is calculated fresh, every time, from its own quantity/unit_price/gst_rate;
    no previous, cached, or accumulated total is ever read or reused.
    """
    taxable_amount = 0.0
    gst_amount = 0.0
    for item in items:
        item_taxable, item_gst = compute_line_taxable_and_gst(item.quantity, item.unit_price, item.gst_rate)
        taxable_amount += item_taxable
        gst_amount += item_gst
    return round(taxable_amount, 2), round(gst_amount, 2)


def compute_sale_grand_total(taxable_amount: float, gst_amount: float, freight: float) -> float:
    """Grand Total = Subtotal (Taxable Amount) + GST + Freight — always, everywhere.
    Takes the already-computed taxable amount and GST so every caller derives
    Grand Total from the exact same numbers it displayed as Subtotal and GST,
    instead of trusting a separately stored grand_total value.
    """
    return round(float(taxable_amount or 0) + float(gst_amount or 0) + float(freight or 0), 2)


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
