# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func
from app.database import get_db
from app.models.models import Sale, PurchaseOrder, Client, PaymentStatus
from app.schemas.dashboard import DashboardStats, ChartData, ChartDataPoint, MonthlyTrend, RecentSale
from app.utils.helpers import compute_sale_taxable_and_gst, compute_sale_grand_total, normalize_client_name
from typing import List, cast, Any

router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"])


@router.get("/stats", response_model=DashboardStats)
def get_stats(db: Session = Depends(get_db)):
    # Fetch every Sale exactly once (eager-loading items so Grand Total can be
    # computed fresh below without N+1 queries) and reuse this single fetch
    # for every metric that needs it. No separate aggregate query, no stored
    # dashboard total, no join that could duplicate a row.
    all_sales = db.query(Sale).options(joinedload(Sale.items)).all()

    # Total revenue: computed with the exact same formula as the Sales Report
    # (Taxable Amount + GST from each sale's own line items, then + Freight),
    # summed once per Sale record — never read from the stored
    # Sale.grand_total column, so this can never drift from the Sales Report
    # or the Sales page, and is recalculated from scratch on every request.
    total_revenue = 0.0
    for s in all_sales:
        taxable_amount, gst_amount = compute_sale_taxable_and_gst(s.items)
        total_revenue += compute_sale_grand_total(taxable_amount, gst_amount, float(s.freight or 0))
    total_revenue = round(total_revenue, 2)

    # Total number of dispatches
    total_orders = len(all_sales)
    # Total unique clients with smart normalization (ignores M/s, LTD, LIMITED, casing)
    all_po_clients = db.query(PurchaseOrder.client_name).all()
    normalized_names = set()
    for row in all_po_clients:
        n = normalize_client_name(row.client_name)
        if n:
            normalized_names.add(n)
    total_clients = len(normalized_names) or 0

    # Delivered orders should be those that have enough challans AND the PO is finished
    delivered_orders = 0
    for s in all_sales:
        if s.delivery_status == "Delivered":
            # 1. Sale docs check
            dispatch_count = 1
            for act in s.activities:
                if act.action == "Items Dispatched":
                    dispatch_count += 1
            url = s.delivery_challan_url or ""
            valid_urls = [u for u in url.split(";") if u and u.strip()]
            has_all_sale_challans = len(valid_urls) >= dispatch_count
            
            # 2. Parent PO check
            po_finished = True
            if s.purchase_order:
                po_finished = s.purchase_order.delivery_status == "Delivered" and s.purchase_order.all_dispatches_marked
            
            if has_all_sale_challans and po_finished:
                delivered_orders += 1
    
    # Payments that are not fully paid
    pending_payments = db.query(func.count(Sale.id)).filter(
        Sale.payment_status.in_([PaymentStatus.PENDING, PaymentStatus.PARTIAL])
    ).scalar() or 0

    return DashboardStats(
        total_revenue=total_revenue,
        total_orders=total_orders,
        total_clients=total_clients,
        delivered_orders=delivered_orders,
        pending_payments=pending_payments,
    )



@router.get("/charts", response_model=ChartData)
def get_charts(db: Session = Depends(get_db)):
    from app.models.models import SaleItem

    # Same GST formula as everywhere else (Taxable Amount = quantity x
    # unit_price, GST = Taxable Amount x gst_rate / 100), expressed in SQL so
    # it can be aggregated efficiently — never SaleItem.total_amount, which is
    # only a snapshot stored at the time the sale was created.
    item_taxable_expr = SaleItem.quantity * SaleItem.unit_price
    item_total_expr = item_taxable_expr + (item_taxable_expr * SaleItem.gst_rate / 100)

    product_rows = (
        db.query(SaleItem.item, func.sum(item_total_expr).label("total"))
        .group_by(SaleItem.item)
        .order_by(func.sum(item_total_expr).desc())
        .limit(10)
        .all()
    )
    sales_by_product = [ChartDataPoint(name=r.item, value=float(r.total or 0)) for r in product_rows]

    # Payment Status distribution
    payment_rows = (
        db.query(Sale.payment_status, func.count(Sale.id).label("cnt"))
        .group_by(Sale.payment_status)
        .all()
    )
    payment_status = [
        ChartDataPoint(
            name=r.payment_status.value if hasattr(r.payment_status, 'value') else str(r.payment_status), 
            value=r.cnt or 0
        ) 
        for r in payment_rows
    ]

    # Monthly Trend - Calculate monthly revenue accurately
    # We'll get items total and freight total separately per month to avoid join multiplication
    from sqlalchemy import extract
    
    monthly_items = (
        db.query(
            func.date_format(Sale.created_at, "%b %Y").label("month"),
            func.sum(item_total_expr).label("items_total"),
            func.count(func.distinct(Sale.id)).label("orders"),
            func.min(Sale.created_at).label("sort_date")
        )
        .join(SaleItem, Sale.id == SaleItem.sale_id)
        .group_by(func.date_format(Sale.created_at, "%b %Y"))
        .all()
    )
    
    monthly_freight = (
        db.query(
            func.date_format(Sale.created_at, "%b %Y").label("month"),
            func.sum(Sale.freight).label("freight_total")
        )
        .group_by(func.date_format(Sale.created_at, "%b %Y"))
        .all()
    )
    
    freight_map = {r.month: float(r.freight_total or 0) for r in monthly_freight}
    
    monthly_trend = [
        MonthlyTrend(
            month=r.month, 
            revenue=float(r.items_total or 0) + freight_map.get(r.month, 0), 
            orders=r.orders or 0
        )
        for r in sorted(monthly_items, key=lambda x: x.sort_date)
    ]

    return ChartData(
        sales_by_product=sales_by_product,
        payment_status=payment_status,
        monthly_trend=monthly_trend,
    )


@router.get("/recent-sales", response_model=List[RecentSale])
def get_recent_sales(limit: int = 6, db: Session = Depends(get_db)):
    rows = (
        db.query(Sale)
        .order_by(Sale.created_at.desc())
        .limit(limit)
        .all()
    )
    res = []
    for r in rows:
        # Calculate price from items + freight
        items_sum = sum((float(it.subtotal or 0) + float(it.gst_amount or 0)) for it in r.items)
        calc_price = items_sum + float(cast(Any, r.freight) or 0)
        
        # Delivery status logic: must have enough challans for all dispatch events
        challan_url = r.delivery_challan_url or ""
        valid_urls = [u for u in challan_url.split(";") if u and u.strip()]
        
        # Count dispatch events: 1 (initial) + N "Items Dispatched" activities
        dispatch_count = 1
        for act in r.activities:
            if act.action == "Items Dispatched":
                dispatch_count += 1
        
        display_status = "Not Delivered"
        if r.delivery_status == "Delivered":
            # 1. Check if this specific sale has enough challans
            has_all_sale_challans = len(valid_urls) >= dispatch_count
            
            # 2. Check if the parent PO is fully delivered (if linked)
            po_finished = True
            if r.purchase_order:
                po_finished = r.purchase_order.delivery_status == "Delivered" and r.purchase_order.all_dispatches_marked
                
            if has_all_sale_challans and po_finished:
                display_status = "Delivered"
            else:
                display_status = "Partial"
        elif r.delivery_status == "Partial":
            display_status = "Partial"
            
        # Fallback and Normalize client name
        raw_name = r.client_name
        if not raw_name or raw_name.strip() == "":
            if r.purchase_order:
                raw_name = r.purchase_order.client_name
        
        # Normalize for consistent display if requested
        display_client_name = raw_name
        if raw_name:
            # We use a slightly less aggressive normalization for display to keep it readable
            # but consistent enough to look "proper"
            display_client_name = raw_name.strip().replace("  ", " ")
            # If it matches our Tata rule, standardize it
            norm = normalize_client_name(cast(str, raw_name))
            if "TATA" in norm:
                display_client_name = "M/s. Tata Projects"
            elif "AFCONS" in norm:
                display_client_name = "M/s. AFCONS Infrastructure Limited"

        res.append(RecentSale(
            id=cast(int, r.id),
            client_name=cast(str, display_client_name) or "Unknown Client",
            product=r.items_display,
            price=calc_price,
            payment_status=r.payment_status.value if hasattr(r.payment_status, 'value') else str(r.payment_status),
            delivery_status=display_status,
            date=r.created_at.isoformat() if r.created_at else "",
            invoice_number=cast(str, r.invoice_number) if r.invoice_number else None,
        ))
    return res
