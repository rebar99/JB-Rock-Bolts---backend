from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime
from app.database import get_db
from app.models.models import Record, PurchaseOrder, Sale, POLineItem
from app.schemas.reports import (
    ReportOut, ReportRow, FulfillmentReportOut, FulfillmentReportRow,
    PendingPOReportOut, PendingPORow
)

router = APIRouter(prefix="/api/reports", tags=["Reports"])


@router.get("", response_model=ReportOut)
def get_report(
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
    product: Optional[str] = None,
    client: Optional[str] = None,
    limit: int = 1000,
    db: Session = Depends(get_db),
):
    q = db.query(Sale)
    if from_date:
        q = q.filter(Sale.created_at >= from_date)
    if to_date:
        # Include the entire day for to_date
        from datetime import time
        to_date_end = datetime.combine(to_date.date(), time(23, 59, 59))
        q = q.filter(Sale.created_at <= to_date_end)
    if product:
        from app.models.models import SaleItem
        q = q.join(Sale.items).filter(SaleItem.item.ilike(f"%{product}%"))
    if client and client.lower() != "all":
        q = q.filter(Sale.client_name.ilike(f"%{client}%"))

    sales = q.order_by(Sale.created_at.desc()).limit(limit).all()

    # Calculate totals from items for 100% accuracy
    rows = []
    total_revenue = 0
    for s in sales:
        # Sum of items for this sale
        items_sum = sum((float(it.subtotal or 0) + float(it.gst_amount or 0)) for it in s.items)
        row_price = items_sum + float(s.freight or 0)
        total_revenue += row_price
        
        rows.append(ReportRow(
            id=s.id,
            date=s.created_at.strftime("%d-%m-%Y") if s.created_at else "",
            client_name=s.client_name,
            product=s.items_display,
            location=s.project or "—",
            po_number=s.po_number,
            invoice_number=s.invoice_number,
            e_way_bill_no=s.e_way_bill_no,
            price=row_price,
            payment_status=s.payment_status.value if hasattr(s.payment_status, 'value') else str(s.payment_status),
            delivery_status="Dispatched",
        ))

    record_count = len(sales)
    avg_order_value = total_revenue / record_count if record_count else 0

    return ReportOut(
        rows=rows,
        total_revenue=round(total_revenue, 2),
        record_count=record_count,
        avg_order_value=round(avg_order_value, 2),
    )


@router.get("/fulfillment", response_model=FulfillmentReportOut)
def get_fulfillment_report(
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
    client: Optional[str] = None,
    db: Session = Depends(get_db),
):
    q = db.query(PurchaseOrder)
    if from_date:
        q = q.filter(PurchaseOrder.created_at >= from_date)
    if to_date:
        from datetime import time
        to_date_end = datetime.combine(to_date.date(), time(23, 59, 59))
        q = q.filter(PurchaseOrder.created_at <= to_date_end)
    if client and client.lower() != "all":
        q = q.filter(PurchaseOrder.client_name.ilike(f"%{client}%"))

    orders = q.order_by(PurchaseOrder.created_at.desc()).all()

    rows = [
        FulfillmentReportRow(
            id=o.id,
            date=o.created_at.strftime("%d-%m-%Y") if o.created_at else "—",
            client_name=o.client_name,
            project=o.project or "—",
            item=o.items_display,
            total_required=o.total_quantity,
            delivered=o.delivered_quantity,
            pending=max(0, o.total_quantity - o.delivered_quantity),
            uom=o.uom or "Nos",
        )
        for o in orders
    ]

    return FulfillmentReportOut(rows=rows)


@router.get("/pending-pos", response_model=PendingPOReportOut)
def get_pending_pos_report(
    db: Session = Depends(get_db),
):
    from app.models.models import DeliveryStatus
    
    # Fetch all POs and filter by delivery_status in Python
    # This is safer than complex SQL queries when using @property logic
    all_pos = db.query(PurchaseOrder).order_by(PurchaseOrder.created_at.desc()).all()
    pending_orders = [o for o in all_pos if o.delivery_status != DeliveryStatus.DELIVERED]
    
    rows = []
    total_subtotal = 0
    total_gst = 0
    total_value = 0
    total_pending_value = 0
    
    for o in pending_orders:
        # Use the grand_total property
        t_sub = float(o.subtotal or 0)
        t_gst = float(o.gst_amount or 0)
        t_total = float(o.grand_total or 0)
        
        total_subtotal += t_sub
        total_gst += t_gst
        total_value += t_total
        
        # Calculate delivered value pro-rata based on delivered qty
        t_qty = float(o.total_qty or 0)
        d_qty = float(o.delivered_qty or 0)
        
        if t_qty > 0:
            p_ratio = max(0, (t_qty - d_qty) / t_qty)
        else:
            p_ratio = 1.0
            
        p_sub = t_sub * p_ratio
        p_gst = t_gst * p_ratio
        p_total = t_total * p_ratio
        
        total_pending_value += p_total
        
        rows.append(PendingPORow(
            id=o.id,
            po_number=o.po_number,
            client_name=o.client_name,
            project=o.project or "—",
            item=o.items_display,
            subtotal=round(t_sub, 2),
            gst_amount=round(t_gst, 2),
            total_value=round(t_total, 2),
            pending_subtotal=round(p_sub, 2),
            pending_gst=round(p_gst, 2),
            pending_total=round(p_total, 2),
            status=o.delivery_status,
            date=o.created_at.strftime("%d-%m-%Y") if o.created_at else "—"
        ))
        
    return PendingPOReportOut(
        rows=rows,
        total_subtotal=round(total_subtotal, 2),
        total_gst=round(total_gst, 2),
        total_value=round(total_value, 2),
        total_pending_value=round(total_pending_value, 2),
        count=len(rows)
    )
