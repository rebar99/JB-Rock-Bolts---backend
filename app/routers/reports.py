from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime
from app.database import get_db
from app.models.models import Record, PurchaseOrder, Sale, POLineItem
from app.schemas.reports import (
    ReportOut, ReportRow, FulfillmentReportOut, FulfillmentReportRow,
    PendingPOReportOut, PendingPORow
)
from app.routers.upload_helpers import (
    read_upload_bytes, parse_import_file, make_excel_response, style_header_row,
)

router = APIRouter(prefix="/api/reports", tags=["Reports"])


# ── Sales Report ──────────────────────────────────────────────────────────────

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
        from datetime import time
        to_date_end = datetime.combine(to_date.date(), time(23, 59, 59))
        q = q.filter(Sale.created_at <= to_date_end)
    if product:
        from app.models.models import SaleItem
        q = q.join(Sale.items).filter(SaleItem.item.ilike(f"%{product}%"))
    if client and client.lower() != "all":
        q = q.filter(Sale.client_name.ilike(f"%{client}%"))

    sales = q.order_by(Sale.created_at.desc()).limit(limit).all()

    rows = []
    total_revenue = 0
    for s in sales:
        row_price = float(s.grand_total or 0)
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
            subtotal=round(float(s.subtotal or 0), 2),
            gst_amount=round(float(s.gst_amount or 0), 2),
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


# ── Fulfillment Report ────────────────────────────────────────────────────────

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
            po_number=o.po_number,
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


# ── Pending POs Report ────────────────────────────────────────────────────────

@router.get("/pending-pos", response_model=PendingPOReportOut)
def get_pending_pos_report(db: Session = Depends(get_db)):
    from app.models.models import DeliveryStatus

    all_pos = db.query(PurchaseOrder).order_by(PurchaseOrder.created_at.desc()).all()
    pending_orders = [o for o in all_pos if o.delivery_status != DeliveryStatus.DELIVERED]

    rows = []
    total_subtotal = total_gst = total_value = total_pending_value = 0

    for o in pending_orders:
        t_sub = float(o.subtotal or 0)
        t_gst = float(o.gst_amount or 0)
        t_total = float(o.grand_total or 0)

        total_subtotal += t_sub
        total_gst += t_gst
        total_value += t_total

        t_qty = float(o.total_qty or 0)
        d_qty = float(o.delivered_qty or 0)
        p_ratio = max(0, (t_qty - d_qty) / t_qty) if t_qty > 0 else 1.0

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
            date=o.created_at.strftime("%d-%m-%Y") if o.created_at else "—",
            uom=o.uom or "Nos",
            total_qty=round(t_qty, 2),
            delivered_qty=round(d_qty, 2),
            pending_qty=round(max(0, t_qty - d_qty), 2),
        ))

    return PendingPOReportOut(
        rows=rows,
        total_subtotal=round(total_subtotal, 2),
        total_gst=round(total_gst, 2),
        total_value=round(total_value, 2),
        total_pending_value=round(total_pending_value, 2),
        count=len(rows)
    )


# ── Combined multi-sheet export ───────────────────────────────────────────────

@router.get("/export-combined")
def export_combined_report(
    sheets: str = Query("fulfillment,sales,pending-pos", description="Comma-separated: fulfillment, sales, pending-pos"),
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
    client: Optional[str] = None,
    product: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Export one or more report sections into a single Excel workbook (one sheet per section)."""
    from openpyxl import Workbook

    requested = [s.strip() for s in sheets.split(",") if s.strip()]
    if not requested:
        raise HTTPException(status_code=400, detail="No sheet types specified")

    wb = Workbook()
    wb.remove(wb.active)  # drop the default empty sheet

    if "fulfillment" in requested:
        ws = wb.create_sheet("Fulfillment Report")
        headers = ["Date", "Client Name", "Project", "Items",
                   "Total Required", "Delivered", "Pending", "UOM"]
        ws.append(headers)
        style_header_row(ws, len(headers))
        data = get_fulfillment_report(from_date=from_date, to_date=to_date, client=client, db=db)
        for r in data.rows:
            ws.append([r.date, r.client_name, r.project, r.item,
                       r.total_required, r.delivered, r.pending, r.uom])

    if "sales" in requested:
        ws = wb.create_sheet("Sales Report")
        headers = ["Date", "Client Name", "Project / Location", "Items",
                   "Invoice No", "PO No", "E-Way Bill No", "Grand Total (₹)", "Payment Status"]
        ws.append(headers)
        style_header_row(ws, len(headers))
        data = get_report(from_date=from_date, to_date=to_date, product=product, client=client, db=db)
        for r in data.rows:
            ws.append([r.date, r.client_name, r.location, r.product,
                       r.invoice_number or "—", r.po_number or "—", r.e_way_bill_no or "—",
                       r.price, r.payment_status])
        ws.append([])
        ws.append(["", "", "", "", "", "", "Total Revenue:", data.total_revenue])
        ws.append(["", "", "", "", "", "", "Record Count:", data.record_count])
        ws.append(["", "", "", "", "", "", "Avg Order Value:", data.avg_order_value])

    if "pending-pos" in requested:
        ws = wb.create_sheet("Pending POs")
        headers = ["Date", "PO Number", "Client Name", "Project", "Items",
                   "Value (Excl. GST) ₹", "GST Amount ₹", "Total Value ₹",
                   "Pending Subtotal ₹", "Pending GST ₹", "Pending Total ₹", "Status"]
        ws.append(headers)
        style_header_row(ws, len(headers))
        data = get_pending_pos_report(db=db)
        for r in data.rows:
            ws.append([r.date, r.po_number, r.client_name, r.project, r.item,
                       r.subtotal, r.gst_amount, r.total_value,
                       r.pending_subtotal, r.pending_gst, r.pending_total,
                       str(r.status.value if hasattr(r.status, "value") else r.status)])
        ws.append([])
        ws.append(["", "", "", "", "Totals:",
                   data.total_subtotal, data.total_gst, data.total_value,
                   "", "", data.total_pending_value])

    if not wb.sheetnames:
        raise HTTPException(status_code=400, detail="No valid sheet types in request")

    sheet_tag = "-".join(s[:3] for s in requested)
    return make_excel_response(wb, f"reports-combined-{sheet_tag}.xlsx")


# ── Combined multi-sheet import ───────────────────────────────────────────────

@router.post("/import-combined")
async def import_combined_report_data(
    file: UploadFile = File(...),
    on_conflict: str = Query("skip", description="skip | update"),
    db: Session = Depends(get_db),
):
    """Import a combined report Excel file.

    Detects sheets by name and routes each to the appropriate importer:
      "Fulfillment Report" → Purchase Orders
      "Pending POs"        → Purchase Orders
      "Sales Report"       → Sales Invoices
    """
    from openpyxl import load_workbook
    from io import BytesIO
    from openpyxl import Workbook as _WB
    from app.routers.purchase_orders import import_purchase_orders
    from app.routers.sales import import_sales

    content = await read_upload_bytes(file)
    try:
        wb = load_workbook(BytesIO(content), data_only=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Cannot read Excel file: {exc}")

    SHEET_ROUTES = {
        "Fulfillment Report": "purchase-orders",
        "Pending POs": "purchase-orders",
        "Sales Report": "sales",
    }

    class _FakeUpload:
        def __init__(self, data: bytes, name: str):
            self._data = data
            self.filename = name

        async def read(self) -> bytes:
            return self._data

    found = [s for s in wb.sheetnames if s in SHEET_ROUTES]
    if not found:
        raise HTTPException(
            status_code=400,
            detail=(f"No recognizable sheets found. Got: {wb.sheetnames}. "
                    f"Expected one or more of: {list(SHEET_ROUTES.keys())}"),
        )

    results: dict = {}
    for sheet_name in found:
        # Re-package just this sheet into its own mini workbook
        mini_wb = _WB()
        mini_ws = mini_wb.active
        mini_ws.title = sheet_name
        for row in wb[sheet_name].iter_rows(values_only=True):
            mini_ws.append(list(row))
        buf = BytesIO()
        mini_wb.save(buf)

        fake = _FakeUpload(buf.getvalue(), f"{sheet_name}.xlsx")
        if SHEET_ROUTES[sheet_name] == "sales":
            result = await import_sales(file=fake, on_conflict=on_conflict, db=db)
        else:
            result = await import_purchase_orders(file=fake, on_conflict=on_conflict, db=db)
        results[sheet_name] = result

    all_errors = [f"[{sn}] {e}" for sn, r in results.items() for e in (r.get("errors") or [])]
    return {
        "sheets": results,
        "created": sum(r.get("created", 0) for r in results.values()),
        "updated": sum(r.get("updated", 0) for r in results.values()),
        "skipped": sum(r.get("skipped", 0) for r in results.values()),
        "errors": all_errors,
    }


# ── Single-sheet Excel export ──────────────────────────────────────────────────

@router.get("/export")
def export_report(
    report_type: str = Query("fulfillment", description="fulfillment | sales | pending-pos"),
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
    client: Optional[str] = None,
    product: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Export a report section to Excel (.xlsx)."""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active

    if report_type == "sales":
        ws.title = "Sales Report"
        headers = [
            "Date", "Client Name", "Project / Location", "Items",
            "Invoice No", "PO No", "E-Way Bill No",
            "Grand Total (₹)", "Payment Status",
        ]
        ws.append(headers)
        style_header_row(ws, len(headers))

        data = get_report(from_date=from_date, to_date=to_date, product=product, client=client, db=db)
        for r in data.rows:
            ws.append([
                r.date, r.client_name, r.location, r.product,
                r.invoice_number or "—", r.po_number or "—", r.e_way_bill_no or "—",
                r.price, r.payment_status,
            ])

        ws.append([])
        ws.append(["", "", "", "", "", "", "Total Revenue:", data.total_revenue])
        ws.append(["", "", "", "", "", "", "Record Count:", data.record_count])
        ws.append(["", "", "", "", "", "", "Avg Order Value:", data.avg_order_value])

        filename = "sales-report.xlsx"

    elif report_type == "pending-pos":
        ws.title = "Pending POs"
        headers = [
            "Date", "PO Number", "Client Name", "Project", "Items",
            "Value (Excl. GST) ₹", "GST Amount ₹", "Total Value ₹",
            "Pending Subtotal ₹", "Pending GST ₹", "Pending Total ₹", "Status",
        ]
        ws.append(headers)
        style_header_row(ws, len(headers))

        data = get_pending_pos_report(db=db)
        for r in data.rows:
            ws.append([
                r.date, r.po_number, r.client_name, r.project, r.item,
                r.subtotal, r.gst_amount, r.total_value,
                r.pending_subtotal, r.pending_gst, r.pending_total,
                str(r.status.value if hasattr(r.status, "value") else r.status),
            ])

        ws.append([])
        ws.append(["", "", "", "", "Totals:", data.total_subtotal, data.total_gst, data.total_value,
                   "", "", data.total_pending_value])

        filename = "pending-pos-report.xlsx"

    else:  # fulfillment (default)
        ws.title = "Fulfillment Report"
        headers = [
            "Date", "Client Name", "Project", "Items",
            "Total Required", "Delivered", "Pending", "UOM",
        ]
        ws.append(headers)
        style_header_row(ws, len(headers))

        data = get_fulfillment_report(from_date=from_date, to_date=to_date, client=client, db=db)
        for r in data.rows:
            ws.append([
                r.date, r.client_name, r.project, r.item,
                r.total_required, r.delivered, r.pending, r.uom,
            ])

        filename = "fulfillment-report.xlsx"

    return make_excel_response(wb, filename)


# ── Excel import (delegates to PO / Sales import logic) ──────────────────────

@router.post("/import")
async def import_report_data(
    file: UploadFile = File(...),
    report_type: str = Query("fulfillment", description="fulfillment | sales | pending-pos"),
    on_conflict: str = Query("skip", description="skip | update"),
    created_by: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Import report data from an Excel or CSV file.

    • fulfillment / pending-pos → creates / updates Purchase Orders
    • sales                     → creates / updates Sales Invoices
    """
    from app.routers.purchase_orders import import_purchase_orders
    from app.routers.sales import import_sales

    # Re-wrap the already-consumed file bytes into a fake UploadFile-compatible object
    content = await read_upload_bytes(file)

    class _FakeUpload:
        def __init__(self, data: bytes, name: str):
            self._data = data
            self.filename = name

        async def read(self) -> bytes:
            return self._data

    fake = _FakeUpload(content, file.filename)

    if report_type == "sales":
        return await import_sales(file=fake, on_conflict=on_conflict, created_by=created_by, db=db)
    else:
        return await import_purchase_orders(file=fake, on_conflict=on_conflict, created_by=created_by, db=db)
