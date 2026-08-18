import re
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.models.models import Sale


def values_equal_for_update(old_val, new_val) -> bool:
    """True if old_val and new_val represent the same value for the purposes
    of "did the user actually change this field" — used by PO/WO update
    endpoints to decide whether a save is a no-op (so last_updated_at/by,
    and therefore the Activity column, are left untouched when nothing
    really changed).

    Datetimes are compared tz-naive-in-UTC on both sides: the DB stores
    naive datetimes, but a client resubmitting an untouched date field
    sends back a tz-aware ISO string (e.g. "...Z"), and naive != aware
    always under Python's datetime comparison even for the same instant —
    without this normalization, every date field would look "changed" on
    every save regardless of whether the user touched it.
    """
    if isinstance(old_val, datetime) or isinstance(new_val, datetime):
        def _naive_utc(v):
            if not isinstance(v, datetime):
                return v
            return v.astimezone(timezone.utc).replace(tzinfo=None) if v.tzinfo else v
        return _naive_utc(old_val) == _naive_utc(new_val)
    return old_val == new_val


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
    n = n.replace("PROJECTS", "").replace("PROJECT", "").replace("PRODUCT", "")
    n = n.replace(".", "").replace(",", "").replace(" ", "").strip()
    return n


def normalize_project_name(name: str) -> str:
    """Collapse a free-text project name down to a comparable key — case,
    spacing, and punctuation only (project names don't carry legal-entity
    suffixes like client names do, so there's nothing else to strip). All
    whitespace is removed entirely (not just collapsed) so "2952 Kochi
    Elevated Metro" and a jammed-together "2952KochiElevatedMetro" typo are
    recognized as the same project.
    """
    if not name:
        return ""
    n = name.upper().replace(".", "").replace(",", "").replace("-", "")
    n = re.sub(r"\s+", "", n).strip()
    return n


def dedupe_names_by_normalized_key(names, normalize_fn):
    """Collapse a list of free-text display names (e.g. every Client.name or
    Project.name row in the DB) down to one canonical entry per
    normalize_fn(name) group, so a dropdown built from this never shows
    "M/s. Afcons" and "M/s. Afcons Infrastructure Limited" as if they were
    different entities.

    The canonical spelling is chosen by how well-formatted it looks (more
    space-separated words wins first — "M/s. Afcons Infrastructure Limited"
    over a jammed-together "M/s. AFCONSINFRASTRUCTURELIMITED" typo that
    happens to share the same normalized key), then by how often that exact
    spelling occurs, then longest, then alphabetically.
    """
    groups: dict = {}
    for name in names:
        if not name:
            continue
        key = normalize_fn(name)
        if not key:
            continue
        groups.setdefault(key, {}).setdefault(name, 0)
        groups[key][name] += 1

    def _word_count(name: str) -> int:
        return len([w for w in name.split() if len(w) > 1])

    result = []
    for variants in groups.values():
        best = max(variants.items(), key=lambda kv: (_word_count(kv[0]), kv[1], len(kv[0]), kv[0]))[0]
        result.append(best)
    return sorted(result, key=lambda s: s.lower())


_REDUCER_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?\s*[*xX/]\s*\d+(?:\.\d+)?)\s*mm", re.IGNORECASE)
_DIA_RE = re.compile(r"(\d+(?:\.\d+)?)\s*mm", re.IGNORECASE)
_PIPE_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*NB", re.IGNORECASE)
# Self-Drilling-Anchor system thread codes ("R32", "R51", ...) — a fixed
# designation, not a variable dimension, so it's checked before _DIA_RE:
# "SDA PLATE R32 200X200X10MM" is grouped by its R32 thread size, not the
# 10mm plate thickness that happens to also appear in the same string.
_R_CODE_RE = re.compile(r"\bR\d+\b", re.IGNORECASE)
# Trailing bare number only (e.g. "GI Pipe Class B 20" -> "20") — deliberately
# NOT a bare search anywhere in the string, so product codes like "JB-9
# RE-BAR COUPLERS" don't have their "9" mistaken for a size.
_BARE_NUMBER_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*$")

# Real item names are typically typed as "<internal part code> - <product
# description> [Make: <manufacturer>]" (e.g. "47080052-Rebar Coupler Dia
# Make:Jbcoupler") — strip both wrapper pieces before grouping, otherwise
# every differently-coded PO/WO line for the same physical product (or every
# differently-typed manufacturer note) would fragment into its own card.
_CODE_PREFIX_RE = re.compile(r"^[A-Za-z0-9]{4,}\s*-+\s*")
_MAKE_SUFFIX_RE = re.compile(r"\bmake\s*[:\-].*$", re.IGNORECASE)

# Known product families get canonicalized to one clean name regardless of
# whatever surrounding description text was typed around them (this is what
# makes "47080052-Rebar Coupler Dia Make:Jbcoupler" and "Rebar Coupler16mmdia"
# both land in the same "Coupler" card). Compound "sda ..." phrases are
# checked before the bare "coupler" keyword so "SDA Coupler R32" lands in its
# own "SDA Coupler" group instead of merging with plain mm-based Couplers.
# Anything not matching a keyword here still gets its own automatic group
# from the cleaned leftover text — so a brand-new product name is never
# dropped, just not specially canonicalized.
_KNOWN_PRODUCT_KEYWORDS = [
    ("sda cross bit", "SDA Cross Bit"),
    ("sda rod", "SDA Rod"),
    ("sda nut", "SDA Nut"),
    ("sda plate", "SDA Plate"),
    ("sda coupler", "SDA Coupler"),
    ("reducer", "Reducer Coupler"),
    ("coupler", "Coupler"),
    ("pipe", "Pipe"),
]


def parse_item_type_and_size(item_name: str) -> tuple:
    """Split a free-typed item name (e.g. "Coupler 16mm", "Pipe 20 NB") into a
    (product_type, size, size_label) triple, purely by pattern-matching the
    text — there is no dedicated Product Type / Dia / Pipe Size column
    anywhere in the schema, so this is the only way to group/report on them.
    Because the category comes from whatever text was typed, a brand-new
    product name (not just Couplers/Pipes) is automatically its own group
    with no code change required.

    Returns:
        product_type: a canonical name for a known product family (e.g.
            "Coupler", "Pipe") if one is recognized anywhere in the text,
            otherwise the item text with the internal part-code prefix,
            "Make: ..." suffix, and size token all stripped, trimmed and
            title-cased. Falls back to "Uncategorized" if nothing is left.
        size: the matched size token, normalized for display (e.g. "16mm",
            "20 NB"), or None if no size was found.
        size_label: "Dia" for coupler-like items, "Pipe Size" for pipe-like
            items, else the generic "Size" — decided from the product_type
            text so it also works for future products.
    """
    raw = (item_name or "").strip()
    if not raw:
        return "Uncategorized", None, "Size"

    # Size is searched for on the code-prefix-stripped text BEFORE the
    # "Make: ..." suffix is removed — real item names like "JB Engineering
    # Make- DCP Anchors 32 mm dia. fully grouted..." use "Make-" mid-sentence
    # (not as a short trailing manufacturer tag), so stripping it first would
    # cut off the "32 mm dia" size that comes after it. The make-suffix strip
    # is still applied afterward, but only to the leftover text used for the
    # product-type name, never to the text the size search runs against.
    working = _CODE_PREFIX_RE.sub("", raw).strip()
    if not working:
        working = raw

    size = None
    remainder = working

    m = _R_CODE_RE.search(working)
    if m:
        size = m.group(0).upper()
        remainder = working[:m.start()] + working[m.end():]
    else:
        m = _REDUCER_SIZE_RE.search(working)
        if m:
            size = f"{m.group(1).replace(' ', '')}mm"
            remainder = working[:m.start()] + working[m.end():]
        else:
            m = _DIA_RE.search(working)
            if m:
                size = f"{m.group(1)}mm"
                remainder = working[:m.start()] + working[m.end():]
            else:
                m = _PIPE_SIZE_RE.search(working)
                if m:
                    size = f"{m.group(1)} NB"
                    remainder = working[:m.start()] + working[m.end():]
                else:
                    m = _BARE_NUMBER_RE.search(working)
                    if m:
                        size = m.group(1)
                        remainder = working[:m.start()] + working[m.end():]

    remainder = _MAKE_SUFFIX_RE.sub("", remainder).strip()
    # Strip leftover punctuation/whitespace the size token left behind
    # (e.g. "Coupler - 16mm" -> "Coupler -" -> "Coupler").
    remainder = re.sub(r"[\s\-,/:]+$", "", re.sub(r"^[\s\-,/:]+", "", remainder)).strip()
    fallback_type = remainder if remainder else working

    product_type = None
    for keyword, canonical in _KNOWN_PRODUCT_KEYWORDS:
        if keyword in fallback_type.lower():
            product_type = canonical
            break
    if not product_type:
        product_type = fallback_type.title() if fallback_type else "Uncategorized"

    type_lower = product_type.lower()
    if "pipe" in type_lower:
        size_label = "Pipe Size"
    elif "coupler" in type_lower:
        size_label = "Dia"
    else:
        size_label = "Size"

    return product_type, size, size_label


def generate_invoice_number(db: Session) -> str:
    year = datetime.now().year
    count = db.query(Sale).filter(
        Sale.invoice_number.like(f"INV-{year}-%")
    ).count()
    return f"INV-{year}-{str(count + 1).zfill(4)}"


def generate_wo_number(db: Session) -> str:
    from app.models.models import WorkOrder
    year = datetime.now().year
    count = db.query(WorkOrder).filter(
        WorkOrder.wo_number.like(f"WO-{year}-%")
    ).count()
    return f"WO-{year}-{str(count + 1).zfill(4)}"


def generate_wo_invoice_number(db: Session) -> str:
    from app.models.models import WorkOrderSale
    year = datetime.now().year
    count = db.query(WorkOrderSale).filter(
        WorkOrderSale.invoice_number.like(f"WINV-{year}-%")
    ).count()
    return f"WINV-{year}-{str(count + 1).zfill(4)}"


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


def recalc_wo_completed_quantities(db: Session, wo) -> None:
    """Rebuild WorkOrder/WOLineItem completed_quantity from actual
    WorkOrderSaleItem rows. Mirrors recalc_po_delivered_quantities exactly —
    always recomputes from scratch (fresh SUM over WorkOrderSaleItem, the
    source of truth for real dispatches) instead of accumulating with
    +=/-=, so the stored value can never drift upward from duplicate/retried
    calls — it is simply overwritten with whatever the Work Order Sales
    table actually contains.
    """
    from sqlalchemy import func
    from app.models.models import WorkOrderSale, WorkOrderSaleItem

    if not wo.line_items:
        return

    wo_sale_ids = [s.id for s in wo.work_order_sales]
    total_all = 0.0
    for li in wo.line_items:
        by_id = db.query(func.sum(WorkOrderSaleItem.quantity)).filter(WorkOrderSaleItem.line_item_id == li.id).scalar() or 0
        by_name = 0.0
        if wo_sale_ids:
            by_name = db.query(func.sum(WorkOrderSaleItem.quantity)).filter(
                WorkOrderSaleItem.sale_id.in_(wo_sale_ids),
                WorkOrderSaleItem.line_item_id.is_(None),
                WorkOrderSaleItem.item.ilike(li.item),
            ).scalar() or 0
        li.completed_quantity = round(max(0, float(by_id) + float(by_name)), 10)
        total_all += li.completed_quantity


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
