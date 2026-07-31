from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session, joinedload
from typing import Optional
from datetime import datetime
from app.database import get_db
from app.models.models import WorkOrder, WorkOrderSale
from app.schemas.work_order_reports import (
    WorkOrderReportRow, WorkOrderReportOut, WorkOrderSaleReportRow, WorkOrderSaleReportOut,
)
from app.routers.upload_helpers import make_excel_response, style_header_row
from app.utils.helpers import compute_sale_taxable_and_gst, compute_sale_grand_total

router = APIRouter(prefix="/api/work-order-reports", tags=["Work Order Reports"])


def _filtered_work_orders(
    db: Session,
    from_date: Optional[datetime],
    to_date: Optional[datetime],
    client: Optional[str],
    project: Optional[str],
    wo_status: Optional[str],
):
    q = db.query(WorkOrder).options(joinedload(WorkOrder.line_items), joinedload(WorkOrder.work_order_sales).joinedload(WorkOrderSale.items))
    if from_date:
        q = q.filter(WorkOrder.created_at >= from_date)
    if to_date:
        from datetime import time
        to_date_end = datetime.combine(to_date.date(), time(23, 59, 59))
        q = q.filter(WorkOrder.created_at <= to_date_end)
    if client and client.lower() != "all":
        q = q.filter(WorkOrder.client_name.ilike(f"%{client}%"))
    if project and project.lower() != "all":
        q = q.filter(WorkOrder.project.ilike(f"%{project}%"))
    if wo_status and wo_status.lower() != "all":
        q = q.filter(WorkOrder.status == wo_status)
    return q.order_by(WorkOrder.created_at.desc()).all()


@router.get("", response_model=WorkOrderReportOut)
def get_work_order_report(
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
    client: Optional[str] = None,
    project: Optional[str] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    orders = _filtered_work_orders(db, from_date, to_date, client, project, status)

    rows = []
    completed = pending = sold = 0
    for o in orders:
        total_q = o.total_qty
        completed_q = o.completed_qty
        pending_q = o.pending_quantity
        # Sales Quantity = actual quantity dispatched via Work Order Sales
        # (real WorkOrderSaleItem rows), not a heuristic — mirrors how the
        # Purchase Order Report's numbers always come from real Sale records.
        sales_q = sum(item.quantity for s in o.work_order_sales for item in s.items)

        if o.status == "Completed":
            completed += 1
        elif o.status == "Pending":
            pending += 1
        if o.work_order_sales:
            sold += 1

        rows.append(WorkOrderReportRow(
            id=o.id,
            wo_number=o.wo_number,
            client_name=o.client_name,
            project=o.project or "—",
            wo_date=o.wo_date.strftime("%d-%m-%Y") if o.wo_date else (o.created_at.strftime("%d-%m-%Y") if o.created_at else "—"),
            item=o.items_display,
            total_quantity=round(total_q, 2),
            completed_quantity=round(completed_q, 2),
            pending_quantity=round(pending_q, 2),
            sales_quantity=round(sales_q, 2),
            status=o.status or "Pending",
            uom=o.uom or "Nos",
            subtotal=round(o.subtotal, 2),
            gst_amount=round(o.gst_amount, 2),
            grand_total=round(o.grand_total, 2),
        ))

    return WorkOrderReportOut(
        rows=rows,
        total_work_orders=len(rows),
        completed_work_orders=completed,
        pending_work_orders=pending,
        sales_work_orders=sold,
    )


# ── Work Order Sales report (mirrors the Purchase Order Report's Sales tab) ──

@router.get("/sales", response_model=WorkOrderSaleReportOut)
def get_work_order_sales_report(
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
    client: Optional[str] = None,
    limit: int = 1000,
    db: Session = Depends(get_db),
):
    q = db.query(WorkOrderSale).options(joinedload(WorkOrderSale.items))
    if from_date:
        q = q.filter(WorkOrderSale.created_at >= from_date)
    if to_date:
        from datetime import time
        to_date_end = datetime.combine(to_date.date(), time(23, 59, 59))
        q = q.filter(WorkOrderSale.created_at <= to_date_end)
    if client and client.lower() != "all":
        q = q.filter(WorkOrderSale.client_name.ilike(f"%{client}%"))

    sales = q.order_by(WorkOrderSale.created_at.desc()).limit(limit).all()

    rows = []
    total_revenue = 0
    for s in sales:
        taxable_amount, gst_amount = compute_sale_taxable_and_gst(s.items)
        row_price = compute_sale_grand_total(taxable_amount, gst_amount, float(s.freight or 0))
        total_revenue += row_price

        rows.append(WorkOrderSaleReportRow(
            id=s.id,
            date=s.created_at.strftime("%d-%m-%Y") if s.created_at else "",
            invoice_number=s.invoice_number,
            wo_number=s.wo_number,
            client_name=s.client_name,
            subtotal=round(taxable_amount, 2),
            gst_amount=round(gst_amount, 2),
            grand_total=round(row_price, 2),
            payment_status=s.payment_status.value if hasattr(s.payment_status, 'value') else str(s.payment_status),
        ))

    record_count = len(sales)
    avg_order_value = total_revenue / record_count if record_count else 0

    return WorkOrderSaleReportOut(
        rows=rows,
        total_revenue=round(total_revenue, 2),
        record_count=record_count,
        avg_order_value=round(avg_order_value, 2),
    )


@router.get("/export")
def export_work_order_report(
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
    client: Optional[str] = None,
    project: Optional[str] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    from openpyxl import Workbook

    orders = _filtered_work_orders(db, from_date, to_date, client, project, status)

    wb = Workbook()
    ws = wb.active
    ws.title = "Work Order Report"

    headers = [
        "Work Order No.", "Client", "Project", "Work Order Date", "Item",
        "Total Quantity", "Completed Quantity", "Pending Quantity", "Sales Quantity", "Status",
    ]
    ws.append(headers)
    style_header_row(ws, len(headers))

    for o in orders:
        sales_q = sum(item.quantity for s in o.work_order_sales for item in s.items)
        ws.append([
            o.wo_number, o.client_name, o.project or "",
            o.wo_date.strftime("%Y-%m-%d") if o.wo_date else "",
            o.items_display,
            round(o.total_qty, 2), round(o.completed_qty, 2), round(o.pending_quantity, 2), round(sales_q, 2),
            o.status or "Pending",
        ])

    return make_excel_response(wb, "work-order-report-export.xlsx")
