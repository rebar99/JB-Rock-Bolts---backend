from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.models import PurchaseOrder, Sale, WorkOrder, WorkOrderSale, HREmployee

router = APIRouter(prefix="/api/documents", tags=["Documents"])

# ── Company info (seller) ─────────────────────────────────────────────────────
SELLER = {
    "name": "RE BAR COUPLER INDIA PRIVATE LIMITED",
    "address1": "10B, Block 23, Industrial Area, Nangal Jarialan",
    "address2": "Distt. Una, Himachal Pradesh 177212",
    "contact": "+91-9679830468",
    "email": "info@jbrockbolts.com",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt_inr(val: float) -> str:
    """Format as Indian comma-separated number with 2 decimals, no ₹ symbol."""
    try:
        val = float(val or 0)
    except (TypeError, ValueError):
        val = 0.0
    s = f"{val:,.2f}"
    # Convert US-style 1,234,567.00 → Indian 12,34,567.00
    parts = s.split(".")
    integer = parts[0].replace(",", "")
    dec = parts[1]
    if len(integer) <= 3:
        return f"{integer}.{dec}"
    last3 = integer[-3:]
    rest = integer[:-3]
    groups = []
    while len(rest) > 2:
        groups.append(rest[-2:])
        rest = rest[:-2]
    if rest:
        groups.append(rest)
    groups.reverse()
    return ",".join(groups) + "," + last3 + "." + dec


def _fmt_date(dt) -> str:
    if not dt:
        return "—"
    return dt.strftime("%d-%m-%Y")


def _amount_words(amount: float) -> str:
    """Convert float amount to Indian words (Crores, Lakhs, Thousands)."""
    ones = [
        "", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
        "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen",
        "Seventeen", "Eighteen", "Nineteen",
    ]
    tens_w = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]

    def two_digit(n: int) -> str:
        if n == 0:
            return ""
        if n < 20:
            return ones[n]
        return tens_w[n // 10] + (" " + ones[n % 10] if n % 10 else "")

    def three_digit(n: int) -> str:
        if n == 0:
            return ""
        h, r = divmod(n, 100)
        res = (ones[h] + " Hundred") if h else ""
        if r:
            res += (" " if res else "") + two_digit(r)
        return res

    n = int(round(float(amount or 0)))
    if n == 0:
        return "Zero Only."

    parts = []
    crores, n = divmod(n, 10_000_000)
    lakhs, n = divmod(n, 100_000)
    thousands, remainder = divmod(n, 1_000)

    if crores:
        parts.append(three_digit(crores) + " Crore")
    if lakhs:
        parts.append(three_digit(lakhs) + " Lakh")
    if thousands:
        parts.append(three_digit(thousands) + " Thousand")
    if remainder:
        parts.append(three_digit(remainder))

    return " ".join(parts).strip() + " Only."


# ── HTML error helper ────────────────────────────────────────────────────────

def _html_not_found(message: str) -> HTMLResponse:
    """Return a user-friendly HTML page instead of a raw JSON 404."""
    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Document Not Found</title>
  <style>
    body {{font-family:'Segoe UI',Arial,sans-serif;display:flex;align-items:center;
           justify-content:center;min-height:80vh;background:#f8f9fa;margin:0}}
    .box {{background:#fff;border-radius:8px;padding:48px 56px;text-align:center;
           box-shadow:0 2px 12px rgba(0,0,0,.08);max-width:420px}}
    h1 {{font-size:18px;color:#dc2626;margin-bottom:12px}}
    p  {{font-size:14px;color:#555;margin:0}}
  </style>
</head>
<body>
  <div class="box">
    <h1>⚠ Document Not Found</h1>
    <p>{message}</p>
  </div>
</body>
</html>"""
    return HTMLResponse(content=html, status_code=404)


# ── PO Document ───────────────────────────────────────────────────────────────

@router.get("/po/{po_id}", response_class=HTMLResponse)
def get_po_document(po_id: int, db: Session = Depends(get_db)):
    po = db.get(PurchaseOrder, po_id)
    if not po:
        return _html_not_found("Purchase order not found. It may have been deleted or the link is invalid.")

    gst_rate = po.gst_rate
    subtotal = po.subtotal
    gst_amount = po.gst_amount
    freight = po.freight
    grand_total = po.grand_total
    pending_qty = po.pending_quantity
    words = _amount_words(grand_total)

    # Determine IGST vs CGST+SGST (inter-state → IGST, same-state → split)
    igst_amt = gst_amount
    cgst_amt = 0.0
    sgst_amt = 0.0
    igst_rate = gst_rate
    cgst_rate = 0.0
    sgst_rate = 0.0

    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Purchase Order – {po.po_number}</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:'Segoe UI',Arial,sans-serif;font-size:12px;color:#111;
          background:#fff;padding:28px 32px}}

    /* ── Top green header ───────────────────────────────── */
    .top-header{{
      background:#2e7d32;color:#fff;text-align:center;
      padding:10px 16px 8px;border-radius:4px 4px 0 0;
    }}
    .top-header .company-name{{font-size:18px;font-weight:700;letter-spacing:.5px;text-transform:uppercase}}
    .top-header .company-addr{{font-size:11px;margin-top:3px;opacity:.92}}
    .top-header .doc-title{{
      font-size:14px;font-weight:700;letter-spacing:2px;text-transform:uppercase;
      margin-top:6px;border-top:1px solid rgba(255,255,255,.4);padding-top:5px
    }}

    /* ── PO meta bar ────────────────────────────────────── */
    .po-meta-bar{{
      display:flex;justify-content:space-between;
      border:1px solid #ccc;border-top:none;
      padding:6px 12px;background:#f9f9f9;font-size:11.5px;
    }}
    .po-meta-bar b{{color:#111}}

    /* ── To / Ship-to ───────────────────────────────────── */
    .address-grid{{
      display:grid;grid-template-columns:1fr 1fr;
      border:1px solid #ccc;border-top:none;
    }}
    .addr-cell{{padding:10px 14px;font-size:11.5px;line-height:1.6}}
    .addr-cell:first-child{{border-right:1px solid #ccc}}
    .addr-label{{font-weight:700;font-size:12px;text-decoration:underline;margin-bottom:4px}}
    .addr-company{{font-weight:700;font-size:12.5px;margin-bottom:2px}}

    /* ── Dear Sir block ─────────────────────────────────── */
    .dear-sir{{
      border:1px solid #ccc;border-top:none;
      padding:8px 14px;font-size:11.5px;line-height:1.7;
    }}

    /* ── Item table ─────────────────────────────────────── */
    .item-table{{width:100%;border-collapse:collapse;margin-top:0;font-size:11.5px}}
    .item-table th{{
      background:#2e7d32;color:#fff;
      padding:6px 8px;text-align:center;
      border:1px solid #ccc;font-weight:600;font-size:11px
    }}
    .item-table td{{
      border:1px solid #ccc;padding:6px 8px;
      vertical-align:top;text-align:center
    }}
    .item-table td.desc{{text-align:left}}
    .item-table td.num{{text-align:right}}

    /* ── Totals block ───────────────────────────────────── */
    .totals-wrap{{display:flex;justify-content:flex-end;border:1px solid #ccc;border-top:none}}
    .totals-table{{border-collapse:collapse;min-width:340px;font-size:11.5px}}
    .totals-table td{{padding:4px 10px;border:1px solid #ccc}}
    .totals-table td.lbl{{font-weight:600;background:#f5f5f5}}
    .totals-table td.val{{text-align:right;font-weight:600;min-width:120px}}
    .totals-table tr.grand td{{background:#2e7d32;color:#fff;font-weight:700;font-size:13px}}

    /* ── Words ──────────────────────────────────────────── */
    .words-bar{{
      border:1px solid #ccc;border-top:none;
      padding:6px 12px;font-size:11.5px;
    }}
    .words-bar b{{font-style:italic}}

    /* ── Terms ──────────────────────────────────────────── */
    .terms{{margin-top:14px;font-size:11px}}
    .terms h4{{font-size:12px;margin-bottom:6px;text-decoration:underline;font-weight:700}}
    .terms table{{width:100%;border-collapse:collapse}}
    .terms td{{padding:3px 6px;vertical-align:top}}
    .terms td:first-child{{font-weight:600;white-space:nowrap;width:140px;color:#2e7d32}}

    /* ── Footer ─────────────────────────────────────────── */
    .sig-footer{{
      margin-top:32px;display:grid;grid-template-columns:1fr 1fr;
      gap:24px;font-size:11px;
    }}
    .sig-block{{border-top:1px solid #999;padding-top:8px;text-align:center}}

    @media print{{
      body{{padding:12px 16px}}
      @page{{size:A4;margin:10mm}}
    }}
  </style>
</head>
<body>

  <!-- ── GREEN HEADER (Client at top) ──────────────────────────── -->
  <div class="top-header">
    <div class="company-name">M/S {po.client_name.upper()}</div>
    <div class="company-addr">
      {po.location or ''}{(' – ' + po.project) if po.project else ''}
    </div>
    <div class="doc-title">PURCHASE ORDER</div>
  </div>

  <!-- ── PO NUMBER & DATE ──────────────────────────────────────── -->
  <div class="po-meta-bar">
    <span><b>P O Number:</b> {po.po_number}</span>
    <span><b>PO Date:</b> {_fmt_date(po.created_at)}</span>
  </div>

  <!-- ── TO / SHIP-TO ─────────────────────────────────────────── -->
  <div class="address-grid">
    <div class="addr-cell">
      <div class="addr-label">To,</div>
      <div class="addr-company">{SELLER['name']}</div>
      <div>{SELLER['address1']}</div>
      <div>{SELLER['address2']}</div>
    </div>
    <div class="addr-cell">
      <div class="addr-label">Ship To:</div>
      <div class="addr-company">M/s. {po.client_name}</div>
      {('<div>' + po.project + '</div>') if po.project else ''}
      <div>{po.location or '—'}</div>
      {('<div><b>PO Validity:</b> ' + _fmt_date(po.validity_date) + '</div>') if po.validity_date else ''}
    </div>
  </div>

  <!-- ── DEAR SIR ───────────────────────────────────────────────── -->
  <div class="dear-sir">
    Dear Sir,<br/>
    &nbsp;&nbsp;&nbsp;&nbsp;With reference to your quotation, we are pleased to place an order on you as per
    the below mentioned item and commercial terms and conditions.
  </div>

  <!-- ── ITEM TABLE ────────────────────────────────────────────── -->
  <table class="item-table">
    <thead>
      <tr>
        <th style="width:36px">Sr.<br/>No.</th>
        <th style="width:52px">Part No.</th>
        <th style="width:56px">HSN<br/>Code</th>
        <th>Item Description</th>
        <th style="width:46px">UOM</th>
        <th style="width:56px">Qty</th>
        <th style="width:76px">Unit Price</th>
        <th style="width:60px">Discount<br/>%</th>
        <th style="width:76px">Less<br/>Discount<br/>Amt</th>
        <th style="width:88px">Taxable<br/>Amount</th>
        <th style="width:96px">Amount</th>
      </tr>
    </thead>
    <tbody>
      {''.join(
          f'''<tr>
            <td>{i+1}</td>
            <td>—</td>
            <td>—</td>
            <td class="desc">
              <b>{li.item}</b>
              {('<br/><br/><b>Project:</b> ' + po.project) if po.project and i == 0 else ''}
              {('<br/><b>Payment Terms:</b> ' + po.payment_terms) if po.payment_terms and i == 0 else ''}
            </td>
            <td>{li.uom or 'Nos'}</td>
            <td class="num">{_fmt_inr(li.quantity)}</td>
            <td class="num">{_fmt_inr(li.unit_price)}</td>
            <td class="num">0%</td>
            <td class="num">—</td>
            <td class="num">{_fmt_inr(li.quantity * li.unit_price)}</td>
            <td class="num">{_fmt_inr(li.quantity * li.unit_price)}</td>
          </tr>'''
          for i, li in enumerate(po.line_items)
      ) if po.line_items else f'''<tr>
        <td>1</td>
        <td>—</td>
        <td>—</td>
        <td class="desc">
          <b>{po.item}</b>
          {('<br/><br/><b>Project:</b> ' + po.project) if po.project else ''}
          {('<br/><b>Payment Terms:</b> ' + po.payment_terms) if po.payment_terms else ''}
        </td>
        <td>{po.uom or 'Nos'}</td>
        <td class="num">{_fmt_inr(po.total_quantity)}</td>
        <td class="num">{_fmt_inr(po.unit_price)}</td>
        <td class="num">0%</td>
        <td class="num">—</td>
        <td class="num">{_fmt_inr(subtotal)}</td>
        <td class="num">{_fmt_inr(subtotal)}</td>
      </tr>'''}
      <!-- blank rows for spacing -->
      <tr><td colspan="11" style="height:22px"></td></tr>
    </tbody>
  </table>

  <!-- ── TOTALS ────────────────────────────────────────────────── -->
  <div class="totals-wrap">
    <table class="totals-table">
      <tr>
        <td class="lbl">Total Taxable Amount</td>
        <td class="val">{_fmt_inr(subtotal)}</td>
      </tr>
      <tr>
        <td class="lbl">{'Add IGST' if igst_rate == 0 and igst_amt > 0 else f'Add IGST&nbsp;@&nbsp;{igst_rate}%'}</td>
        <td class="val">{_fmt_inr(igst_amt)}</td>
      </tr>
      <tr>
        <td class="lbl">{'Add CGST' if cgst_rate == 0 and cgst_amt > 0 else f'Add CGST&nbsp;@&nbsp;{cgst_rate}%'}</td>
        <td class="val">{_fmt_inr(cgst_amt) if cgst_amt else '—'}</td>
      </tr>
      <tr>
        <td class="lbl">{'Add SGST' if sgst_rate == 0 and sgst_amt > 0 else f'Add SGST&nbsp;@&nbsp;{sgst_rate}%'}</td>
        <td class="val">{_fmt_inr(sgst_amt) if sgst_amt else '—'}</td>
      </tr>
      <tr>
        <td class="lbl">P &amp; F (Freight)</td>
        <td class="val">{_fmt_inr(freight + sum(li.freight for li in po.line_items) if po.line_items else freight)}</td>
      </tr>
      <tr>
        <td class="lbl">Round Off</td>
        <td class="val">—</td>
      </tr>
      <tr class="grand">
        <td class="lbl">Grand Total</td>
        <td class="val">{_fmt_inr(grand_total)}</td>
      </tr>
    </table>
  </div>

  <!-- ── AMOUNT IN WORDS ───────────────────────────────────────── -->
  <div class="words-bar">
    <b>IN WORDS: {words}</b>
  </div>

  {f"""<!-- ── REMARKS ────────────────────────────────────────── -->
  <div style="margin-top:14px;font-size:11px;">
    <h4 style="font-size:12px;margin-bottom:6px;text-decoration:underline;font-weight:700">Remarks:</h4>
    <div style="border:1px solid #ccc;padding:8px 12px;background:#f9f9f9;border-radius:4px;white-space:pre-wrap;">{po.remark}</div>
  </div>""" if po.remark else ''}

  <!-- ── TERMS & CONDITIONS ────────────────────────────────────── -->
  <div class="terms">
    <h4>Terms &amp; Conditions:</h4>
    <table>
      <tr>
        <td>1. ACKNOWLEDGEMENT COPY</td>
        <td>The Supplier issuing written acceptance of the Purchase Order. Please confirm acceptance of
            this order to Company's materials Department.</td>
      </tr>
      <tr>
        <td>2. DELIVERY</td>
        <td>Immediate.</td>
      </tr>
      <tr>
        <td>3. PRICE</td>
        <td>The above agreed price shall remain firm and fixed till the completion of supply at site.</td>
      </tr>
      <tr>
        <td>4. TAX</td>
        <td>GST included in the calculated amount. In case of any default by the Supplier in compliance
            with the provisions of the GST Act or any such tax statutes as applicable to the Supplier,
            any loss caused on account of the same to us shall be indemnified and reimbursed by the
            Supplier.</td>
      </tr>
      <tr>
        <td>5. TRANSPORTATION</td>
        <td>FOR At Site.</td>
      </tr>
      <tr>
        <td>6. DELIVERY ADDRESS</td>
        <td>{po.location or '—'}{(', ' + po.project) if po.project else ''}.</td>
      </tr>
      <tr>
        <td>7. CERTIFICATE</td>
        <td>Materials Test Certificate shall be provided by Supplier along with materials at site.</td>
      </tr>
      <tr>
        <td>8. CONTACT PERSON</td>
        <td>{SELLER['contact']}</td>
      </tr>
      <tr>
        <td>9. PAYMENT TERMS</td>
        <td>{po.payment_terms or 'As per discussion.'}</td>
      </tr>
      <tr>
        <td>10. VALIDITY</td>
        <td>This PO is valid till {_fmt_date(po.validity_date)}.</td>
      </tr>
      <tr>
        <td>11. JURISDICTION</td>
        <td>As per applicable laws.</td>
      </tr>
    </table>
  </div>

  <p style="margin-top:14px;font-size:11px;color:#555">
    We are sending this order in duplicate, please return one copy duly sealed and signed at your
    end as a token of your acceptance.
  </p>

  <!-- ── SIGNATURE FOOTER ──────────────────────────────────────── -->
  <div class="sig-footer">
    <div class="sig-block">
      Thanking You.<br/>
      Yours faithfully,<br/><br/>
      <b>For {po.client_name}</b><br/><br/><br/>
      Authorised Signatory
    </div>
    <div class="sig-block">
      Read and Accepted<br/><br/><br/><br/>
      <b>{SELLER['name']}</b><br/><br/>
      Authorised Signatory
    </div>
  </div>

</body>
</html>"""
    return HTMLResponse(content=html)


# ── Work Order Document ─────────────────────────────────────────────────────

@router.get("/wo/{wo_id}", response_class=HTMLResponse)
def get_wo_document(wo_id: int, db: Session = Depends(get_db)):
    wo = db.get(WorkOrder, wo_id)
    if not wo:
        return _html_not_found("Work order not found. It may have been deleted or the link is invalid.")

    subtotal = wo.subtotal
    gst_amount = wo.gst_amount
    freight = wo.freight
    grand_total = wo.grand_total
    words = _amount_words(grand_total)
    items_freight = sum(li.freight for li in wo.line_items) if wo.line_items else freight

    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Work Order – {wo.wo_number}</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:'Segoe UI',Arial,sans-serif;font-size:12px;color:#111;
          background:#fff;padding:28px 32px}}

    .top-header{{
      background:#2e7d32;color:#fff;text-align:center;
      padding:10px 16px 8px;border-radius:4px 4px 0 0;
    }}
    .top-header .company-name{{font-size:18px;font-weight:700;letter-spacing:.5px;text-transform:uppercase}}
    .top-header .company-addr{{font-size:11px;margin-top:3px;opacity:.92}}
    .top-header .doc-title{{
      font-size:14px;font-weight:700;letter-spacing:2px;text-transform:uppercase;
      margin-top:6px;border-top:1px solid rgba(255,255,255,.4);padding-top:5px
    }}

    .wo-meta-bar{{
      display:flex;justify-content:space-between;
      border:1px solid #ccc;border-top:none;
      padding:6px 12px;background:#f9f9f9;font-size:11.5px;
    }}
    .wo-meta-bar b{{color:#111}}

    .address-grid{{
      display:grid;grid-template-columns:1fr 1fr;
      border:1px solid #ccc;border-top:none;
    }}
    .addr-cell{{padding:10px 14px;font-size:11.5px;line-height:1.6}}
    .addr-cell:first-child{{border-right:1px solid #ccc}}
    .addr-label{{font-weight:700;font-size:12px;text-decoration:underline;margin-bottom:4px}}
    .addr-company{{font-weight:700;font-size:12.5px;margin-bottom:2px}}

    .item-table{{width:100%;border-collapse:collapse;margin-top:0;font-size:11.5px}}
    .item-table th{{
      background:#2e7d32;color:#fff;
      padding:6px 8px;text-align:center;
      border:1px solid #ccc;font-weight:600;font-size:11px
    }}
    .item-table td{{
      border:1px solid #ccc;padding:6px 8px;
      vertical-align:top;text-align:center
    }}
    .item-table td.desc{{text-align:left}}
    .item-table td.num{{text-align:right}}

    .totals-wrap{{display:flex;justify-content:flex-end;border:1px solid #ccc;border-top:none}}
    .totals-table{{border-collapse:collapse;min-width:340px;font-size:11.5px}}
    .totals-table td{{padding:4px 10px;border:1px solid #ccc}}
    .totals-table td.lbl{{font-weight:600;background:#f5f5f5}}
    .totals-table td.val{{text-align:right;font-weight:600;min-width:120px}}
    .totals-table tr.grand td{{background:#2e7d32;color:#fff;font-weight:700;font-size:13px}}

    .words-bar{{
      border:1px solid #ccc;border-top:none;
      padding:6px 12px;font-size:11.5px;
    }}
    .words-bar b{{font-style:italic}}

    .terms{{margin-top:14px;font-size:11px}}
    .terms h4{{font-size:12px;margin-bottom:6px;text-decoration:underline;font-weight:700}}
    .terms table{{width:100%;border-collapse:collapse}}
    .terms td{{padding:3px 6px;vertical-align:top}}
    .terms td:first-child{{font-weight:600;white-space:nowrap;width:170px;color:#2e7d32}}

    .sig-footer{{
      margin-top:32px;display:grid;grid-template-columns:1fr 1fr;
      gap:24px;font-size:11px;
    }}
    .sig-block{{border-top:1px solid #999;padding-top:8px;text-align:center}}

    @media print{{
      body{{padding:12px 16px}}
      @page{{size:A4;margin:10mm}}
    }}
  </style>
</head>
<body>

  <div class="top-header">
    <div class="company-name">M/S {wo.client_name.upper()}</div>
    <div class="company-addr">
      {wo.site_location or ''}{(' – ' + wo.project) if wo.project else ''}
    </div>
    <div class="doc-title">WORK ORDER</div>
  </div>

  <div class="wo-meta-bar">
    <span><b>Work Order Number:</b> {wo.wo_number}</span>
    <span><b>WO Date:</b> {_fmt_date(wo.wo_date or wo.created_at)}</span>
  </div>

  <div class="address-grid">
    <div class="addr-cell">
      <div class="addr-label">From,</div>
      <div class="addr-company">{SELLER['name']}</div>
      <div>{SELLER['address1']}</div>
      <div>{SELLER['address2']}</div>
    </div>
    <div class="addr-cell">
      <div class="addr-label">Client / Site:</div>
      <div class="addr-company">M/s. {wo.client_name}</div>
      {('<div>' + wo.project + '</div>') if wo.project else ''}
      <div>{wo.site_location or '—'}</div>
      {('<div><b>Engineer/Supervisor:</b> ' + wo.engineer_name + '</div>') if wo.engineer_name else ''}
      <div><b>Priority:</b> {wo.priority or 'Medium'}</div>
      {('<div><b>Start Date:</b> ' + _fmt_date(wo.start_date) + '</div>') if wo.start_date else ''}
      {('<div><b>Target Completion:</b> ' + _fmt_date(wo.target_completion_date) + '</div>') if wo.target_completion_date else ''}
      <div><b>Status:</b> {wo.status or 'Pending'}</div>
    </div>
  </div>

  {f'''<div class="wo-meta-bar" style="display:block">
    <b>Work Description:</b> {wo.work_description}
  </div>''' if wo.work_description else ''}

  <table class="item-table">
    <thead>
      <tr>
        <th style="width:36px">Sr.<br/>No.</th>
        <th>Item Description</th>
        <th style="width:46px">UOM</th>
        <th style="width:56px">Qty</th>
        <th style="width:70px">Completed</th>
        <th style="width:70px">Pending</th>
        <th style="width:76px">Unit Price</th>
        <th style="width:88px">Taxable<br/>Amount</th>
        <th style="width:96px">Amount</th>
      </tr>
    </thead>
    <tbody>
      {''.join(
          f'''<tr>
            <td>{i+1}</td>
            <td class="desc"><b>{li.item}</b></td>
            <td>{li.uom or 'Nos'}</td>
            <td class="num">{_fmt_inr(li.quantity)}</td>
            <td class="num">{_fmt_inr(li.completed_quantity)}</td>
            <td class="num">{_fmt_inr(max(0, li.quantity - li.completed_quantity))}</td>
            <td class="num">{_fmt_inr(li.unit_price)}</td>
            <td class="num">{_fmt_inr(li.quantity * li.unit_price)}</td>
            <td class="num">{_fmt_inr(li.quantity * li.unit_price)}</td>
          </tr>'''
          for i, li in enumerate(wo.line_items)
      ) if wo.line_items else f'''<tr>
        <td>1</td>
        <td class="desc"><b>{wo.item}</b></td>
        <td>{wo.uom or 'Nos'}</td>
        <td class="num">{_fmt_inr(wo.total_quantity)}</td>
        <td class="num">0.00</td>
        <td class="num">{_fmt_inr(wo.total_quantity)}</td>
        <td class="num">{_fmt_inr(wo.unit_price)}</td>
        <td class="num">{_fmt_inr(subtotal)}</td>
        <td class="num">{_fmt_inr(subtotal)}</td>
      </tr>'''}
      <tr><td colspan="9" style="height:22px"></td></tr>
    </tbody>
  </table>

  <div class="totals-wrap">
    <table class="totals-table">
      <tr>
        <td class="lbl">Total Taxable Amount</td>
        <td class="val">{_fmt_inr(subtotal)}</td>
      </tr>
      <tr>
        <td class="lbl">Add GST</td>
        <td class="val">{_fmt_inr(gst_amount)}</td>
      </tr>
      <tr>
        <td class="lbl">P &amp; F (Freight)</td>
        <td class="val">{_fmt_inr(items_freight)}</td>
      </tr>
      <tr class="grand">
        <td class="lbl">Grand Total</td>
        <td class="val">{_fmt_inr(grand_total)}</td>
      </tr>
    </table>
  </div>

  <div class="words-bar">
    <b>IN WORDS: {words}</b>
  </div>

  {f"""<div style="margin-top:14px;font-size:11px;">
    <h4 style="font-size:12px;margin-bottom:6px;text-decoration:underline;font-weight:700">Remarks:</h4>
    <div style="border:1px solid #ccc;padding:8px 12px;background:#f9f9f9;border-radius:4px;white-space:pre-wrap;">{wo.remarks}</div>
  </div>""" if wo.remarks else ''}

  <div class="sig-footer">
    <div class="sig-block">
      Issued By,<br/><br/><br/>
      <b>{SELLER['name']}</b><br/><br/>
      Authorised Signatory
    </div>
    <div class="sig-block">
      Received &amp; Accepted<br/><br/><br/><br/>
      <b>{wo.engineer_name or wo.client_name}</b><br/><br/>
      Signature
    </div>
  </div>

</body>
</html>"""
    return HTMLResponse(content=html)


# ── Sales Invoice (Tax Invoice format) ────────────────────────────────────────

@router.get("/invoice/{sale_id}", response_class=HTMLResponse)
def get_invoice_document(sale_id: int, download: bool = False, db: Session = Depends(get_db)):
    sale = db.get(Sale, sale_id)
    if not sale:
        return _html_not_found("Sale not found. It may have been deleted or the link is invalid.")

    # Load linked PO and Client for extra fields
    po = db.get(PurchaseOrder, sale.po_id) if sale.po_id else None
    client = po.client_rel if po else None

    # Totals are now aggregate on the Sale model
    subtotal = sale.subtotal
    gst_amount = sale.gst_amount
    freight = sale.freight
    grand_total = sale.grand_total
    
    # Calculate delivered/pending relative to the PO
    # This is a simplification; ideally we'd track per line item
    total_delivered = po.delivered_quantity if po else 0
    pending_qty     = po.pending_quantity if po else 0
    words           = _amount_words(grand_total)

    # Default to IGST (inter-state) since client state is no longer tracked
    is_intra_state = False
    
    gst_rate   = sale.items[0].gst_rate if sale.items else 18.0
    if is_intra_state:
        igst_rate = 0.0
        igst_amt  = 0.0
        cgst_rate = gst_rate / 2
        sgst_rate = gst_rate / 2
        cgst_amt  = sale.gst_amount / 2
        sgst_amt  = sale.gst_amount / 2
    else:
        igst_rate = gst_rate
        igst_amt  = sale.gst_amount
        cgst_rate = 0.0
        cgst_amt  = 0.0
        sgst_rate = 0.0
        sgst_amt  = 0.0

    inv_no     = sale.invoice_number or "DRAFT"
    inv_date   = _fmt_date(sale.created_at)
    po_date    = _fmt_date(po.created_at)     if po else "—"
    deliv_date = _fmt_date(sale.updated_at)
    pay_terms  = (sale.payment_terms if sale.payment_terms else
                  po.payment_terms if po and po.payment_terms else
                  sale.payment_note or "As per discussion")
    location   = (client.location if client and client.location else po.location if po else "—")
    project    = sale.project or "—"
    ship_to_addr = sale.ship_to or location
    bill_to_addr = sale.bill_to or sale.client_name
    dispatched_via = sale.dispatched_through or "—"
    buyer_order = sale.buyers_order_no or sale.po_number
    hsn_sac    = sale.hsn_code or "—"

    # Fake IRN / Ack for display (real e-Invoice requires GST portal integration)
    import hashlib, time
    irn_seed = f"{inv_no}{sale.client_name}{sale.grand_total}"
    irn_hash = hashlib.sha256(irn_seed.encode()).hexdigest()
    irn      = f"{irn_hash[:32]}-{irn_hash[32:48]}"
    ack_no   = str(abs(hash(irn_seed)))[:15]

    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Tax Invoice – {inv_no}</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:'Segoe UI',Arial,sans-serif;font-size:11.5px;color:#111;
          background:#fff;padding:24px 32px;max-width:900px;margin:0 auto}}

    /* ── Page header ──────────────────────────────────────────── */
    .page-header{{
      display:grid;grid-template-columns:80px 1fr 120px;
      align-items:center;margin-bottom:6px;
    }}
    .deco-lines span{{
      display:block;height:3px;background:#333;margin-bottom:4px;
    }}
    .deco-lines span:first-child{{width:40px}}
    .deco-lines span:last-child{{width:28px}}
    .page-title{{text-align:center;font-size:17px;font-weight:700;letter-spacing:.5px}}
    .e-inv-box{{text-align:right;font-size:10px;font-weight:600;color:#444}}
    .qr-placeholder{{
      width:76px;height:76px;border:1px solid #999;
      display:flex;align-items:center;justify-content:center;
      font-size:8px;color:#888;margin-left:auto;margin-top:3px;
    }}

    /* ── IRN bar ──────────────────────────────────────────────── */
    .irn-bar{{font-size:10.5px;margin-bottom:8px;line-height:1.8}}
    .irn-bar .row{{display:flex;gap:6px}}
    .irn-bar .lbl{{font-weight:600;min-width:70px}}

    /* ── Main two-column block ────────────────────────────────── */
    .main-grid{{display:grid;grid-template-columns:1fr 1fr;border:1px solid #555}}

    /* Seller cell */
    .seller-cell{{padding:10px 12px;font-size:11px;line-height:1.75;border-right:1px solid #555}}
    .seller-name{{font-weight:700;font-size:12px}}

    /* Invoice details grid (right side) */
    .inv-details{{font-size:10.5px}}
    .inv-details table{{width:100%;border-collapse:collapse;height:100%}}
    .inv-details td{{
      border:1px solid #555;padding:3px 7px;vertical-align:top;line-height:1.6
    }}
    .inv-details td.lbl{{font-weight:600;color:#222;background:#f7f7f7;white-space:nowrap}}
    .inv-details td.val{{font-weight:600}}
    .inv-details td.date-lbl{{font-weight:600;font-size:10px;color:#555}}

    /* ── Consignee / Buyer block ──────────────────────────────── */
    .party-grid{{
      display:grid;grid-template-columns:1fr 1fr;
      border:1px solid #555;border-top:none;
    }}
    .party-cell{{padding:8px 12px;font-size:11px;line-height:1.75}}
    .party-cell:first-child{{border-right:1px solid #555}}
    .party-type{{font-weight:700;font-size:10.5px;text-decoration:underline;
                 margin-bottom:3px;color:#333}}
    .party-name{{font-weight:700;font-size:12px}}

    /* ── Item table ───────────────────────────────────────────── */
    .item-table{{
      width:100%;border-collapse:collapse;
      border:1px solid #555;border-top:none;
      font-size:11px;
    }}
    .item-table th{{
      border:1px solid #555;padding:5px 7px;
      text-align:center;font-weight:700;font-size:10.5px;
      background:#f0f0f0;
    }}
    .item-table td{{
      border:1px solid #555;padding:5px 7px;
      vertical-align:top;
    }}
    .item-table td.num{{text-align:right}}
    .item-table td.ctr{{text-align:center}}

    /* ── Totals section ───────────────────────────────────────── */
    .totals-section{{
      display:grid;grid-template-columns:1fr auto;
      border:1px solid #555;border-top:none;
    }}
    .words-cell{{
      padding:8px 12px;font-size:11px;line-height:1.6;
      border-right:1px solid #555;
    }}
    .totals-cell{{min-width:280px}}
    .totals-cell table{{width:100%;border-collapse:collapse}}
    .totals-cell td{{
      border:1px solid #555;padding:4px 10px;font-size:11px;
    }}
    .totals-cell td.lbl{{font-weight:600;background:#f7f7f7;width:160px}}
    .totals-cell td.val{{text-align:right;font-weight:600}}
    .totals-cell tr.grand td{{
      font-weight:700;font-size:12px;background:#1a1a2e;color:#fff;
    }}

    /* ── GST breakup ──────────────────────────────────────────── */
    .gst-table{{
      width:100%;border-collapse:collapse;
      border:1px solid #555;border-top:none;
      font-size:10.5px;
    }}
    .gst-table th{{
      border:1px solid #555;padding:4px 7px;
      background:#f0f0f0;font-weight:700;text-align:center;
    }}
    .gst-table td{{border:1px solid #555;padding:4px 7px;text-align:center}}
    .gst-table td.num{{text-align:right}}

    /* ── Declaration & sign ───────────────────────────────────── */
    .bottom-grid{{
      display:grid;grid-template-columns:1fr 1fr;
      border:1px solid #555;border-top:none;min-height:80px;
    }}
    .decl-cell{{
      padding:8px 12px;font-size:10.5px;line-height:1.7;
      border-right:1px solid #555;
    }}
    .sign-cell{{
      padding:8px 12px;font-size:10.5px;text-align:right;
      display:flex;flex-direction:column;justify-content:space-between;
    }}
    .sign-cell .company{{font-weight:700;font-size:11px}}
    .sign-cell .sig-line{{
      border-top:1px solid #555;width:160px;
      margin-left:auto;padding-top:4px;font-size:10px;
    }}

    .footer-note{{
      text-align:center;font-size:10.5px;color:#555;
      margin-top:8px;font-style:italic;
    }}

    @media print{{
      body{{padding:8px 12px}}
      @page{{size:A4;margin:8mm}}
    }}
  </style>
</head>
<body>

  <!-- ══ PAGE HEADER ══════════════════════════════════════════════ -->
  <div class="page-header">
    <div class="deco-lines">
      <span></span>
      <span></span>
    </div>
    <div class="page-title">Tax Invoice</div>
    <div class="e-inv-box">
      e-Invoice
      <div class="qr-placeholder">QR Code</div>
    </div>
  </div>

  <!-- ══ IRN / ACK ════════════════════════════════════════════════ -->
  <div class="irn-bar">
    <div class="row"><span class="lbl">IRN</span><span>: {irn}</span></div>
    <div class="row"><span class="lbl">Ack No.</span><span>: {ack_no}</span></div>
    <div class="row"><span class="lbl">Ack Date</span><span>: {inv_date}</span></div>
  </div>

  <!-- ══ SELLER + INVOICE DETAILS ═════════════════════════════════ -->
  <div class="main-grid">

    <!-- Left: Seller -->
    <div class="seller-cell">
      <div class="seller-name">{SELLER['name']} (FY{_fy_label(sale.created_at)})</div>
      <div>Regd. Office: {SELLER['address1']},</div>
      <div>{SELLER['address2']} India</div>
      <div>PLANT ADD.: VPO PALAKWAHA, TEHSIL HAROLI,</div>
      <div>Una, 177220</div>
      <div>IEC CODE NO.: 2216900613</div>
      <div>E-Mail: {SELLER['email']}</div>
    </div>

    <!-- Right: Invoice detail grid -->
    <div class="inv-details">
      <table>
        <tr>
          <td class="lbl">Invoice No.</td>
          <td class="val">{inv_no}</td>
          <td class="lbl">e-Way Bill No.</td>
          <td class="val">{sale.e_way_bill_no or '—'}</td>
        </tr>
        <tr>
          <td class="lbl">Delivery Note</td>
          <td>—</td>
          <td class="date-lbl">Dated</td>
          <td class="val">{inv_date}</td>
        </tr>
        <tr>
          <td class="lbl">Reference No. &amp; Date.</td>
          <td colspan="3">—</td>
        </tr>
        <tr>
          <td class="lbl">Mode/Terms of Payment</td>
          <td colspan="3">{pay_terms}</td>
        </tr>
        <tr>
          <td class="lbl">Buyer's Order No.</td>
          <td class="val">{buyer_order}</td>
          <td class="date-lbl">Dated</td>
          <td class="val">{po_date}</td>
        </tr>
        <tr>
          <td class="lbl">Dispatch Doc No.</td>
          <td>—</td>
          <td class="date-lbl">Delivery Note Date</td>
          <td class="val">{deliv_date}</td>
        </tr>
        <tr>
          <td class="lbl">Dispatched through</td>
          <td colspan="3">{dispatched_via}</td>
        </tr>
        <tr>
          <td class="lbl">Destination</td>
          <td colspan="3">{location}</td>
        </tr>
        <tr>
          <td class="lbl">Terms of Delivery</td>
          <td colspan="3">{project}</td>
        </tr>
      </table>
    </div>
  </div>

  <!-- ══ CONSIGNEE (SHIP TO) + BUYER (BILL TO) ════════════════════ -->
  <div class="party-grid">
    <div class="party-cell">
      <div class="party-type">Consignee (Ship to)</div>
      <div class="party-name">{sale.client_name}</div>
      <div style="white-space:pre-line">{ship_to_addr}</div>
    </div>
    <div class="party-cell">
      <div class="party-type">Buyer (Bill to)</div>
      <div class="party-name">{sale.client_name}</div>
      <div style="white-space:pre-line">{bill_to_addr}</div>
    </div>
  </div>

  <!-- ══ ITEM TABLE ════════════════════════════════════════════════ -->
  <table class="item-table">
    <thead>
      <tr>
        <th style="width:30px">Sl<br/>No.</th>
        <th>Description of Goods</th>
        <th style="width:70px">HSN/SAC</th>
        <th style="width:90px">Quantity</th>
        <th style="width:80px">Rate</th>
        <th style="width:46px">per</th>
        <th style="width:52px">Disc.&nbsp;%</th>
        <th style="width:100px">Amount</th>
      </tr>
    </thead>
    <tbody>
      {''.join(
          f'''<tr>
            <td class="ctr">{i+1}</td>
            <td>
              <b>{it.item}</b>
              {('<br/><span style="font-size:10px;color:#555">' + sale.project + '</span>') if sale.project and i == 0 else ''}
            </td>
            <td class="ctr">{hsn_sac}</td>
            <td class="num">{_fmt_inr(it.quantity)}&nbsp;{it.uom or 'Nos'}</td>
            <td class="num">{_fmt_inr(it.unit_price)}</td>
            <td class="ctr">{it.uom or 'Nos'}</td>
            <td class="ctr">—</td>
            <td class="num">{_fmt_inr(it.subtotal)}</td>
          </tr>'''
          for i, it in enumerate(sale.items)
      )}
      <!-- blank rows for spacing on A4 -->
      {''.join('<tr>' + '<td style="height:20px"></td>'*8 + '</tr>' for _ in range(7))}
    </tbody>
  </table>

  <!-- ══ TOTALS + AMOUNT IN WORDS ══════════════════════════════════ -->
  <div class="totals-section">

    <!-- Left: Amount in words -->
    <div class="words-cell">
      <div style="font-weight:700;margin-bottom:4px">Amount Chargeable (in words)</div>
      <div style="font-style:italic">INR {words}</div>
      {('<div style="margin-top:8px;font-size:10.5px"><b>Payment Note:</b> ' + sale.payment_note + '</div>') if sale.payment_note else ''}
      <div style="margin-top:12px;font-size:10px;color:#666">
        Total Delivered (PO): {_fmt_inr(total_delivered)} &nbsp;|&nbsp;
        Balance Pending (PO): {_fmt_inr(pending_qty)}
      </div>
    </div>

    <!-- Right: Totals -->
    <div class="totals-cell">
      <table>
        <tr>
          <td class="lbl">Taxable Value</td>
          <td class="val">{_fmt_inr(sale.subtotal)}</td>
        </tr>
        <tr>
          <td class="lbl">IGST @ {igst_rate}%</td>
          <td class="val">{_fmt_inr(igst_amt) if igst_amt else '—'}</td>
        </tr>
        <tr>
          <td class="lbl">CGST @ {cgst_rate}%</td>
          <td class="val">{_fmt_inr(cgst_amt) if cgst_amt else '—'}</td>
        </tr>
        <tr>
          <td class="lbl">SGST @ {sgst_rate}%</td>
          <td class="val">{_fmt_inr(sgst_amt) if sgst_amt else '—'}</td>
        </tr>
        <tr>
          <td class="lbl">Freight / P&amp;F{' (With GST)' if getattr(sale, 'freight_gst_type', None) == 'With GST' else ''}</td>
          <td class="val">{_fmt_inr(sale.freight)}</td>
        </tr>
        <tr>
          <td class="lbl">Round Off</td>
          <td class="val">—</td>
        </tr>
        <tr class="grand">
          <td class="lbl">Grand Total</td>
          <td class="val">₹&nbsp;{_fmt_inr(sale.grand_total)}</td>
        </tr>
      </table>
    </div>
  </div>

  <!-- ══ GST TAX ANALYSIS ══════════════════════════════════════════ -->
  <table class="gst-table">
    <thead>
      <tr>
        <th>HSN/SAC</th>
        <th>Taxable Value</th>
        <th colspan="2">IGST</th>
        <th colspan="2">CGST</th>
        <th colspan="2">SGST/UTGST</th>
        <th>Total Tax Amount</th>
      </tr>
      <tr>
        <th></th><th></th>
        <th>Rate</th><th>Amount</th>
        <th>Rate</th><th>Amount</th>
        <th>Rate</th><th>Amount</th>
        <th></th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td class="ctr">{hsn_sac}</td>
        <td class="num">{_fmt_inr(sale.subtotal)}</td>
        <td class="ctr">{f"{igst_rate}%" if igst_rate else "—"}</td>
        <td class="num">{_fmt_inr(igst_amt) if igst_amt else "—"}</td>
        <td class="ctr">{f"{cgst_rate}%" if cgst_rate else "—"}</td>
        <td class="num">{_fmt_inr(cgst_amt) if cgst_amt else "—"}</td>
        <td class="ctr">{f"{sgst_rate}%" if sgst_rate else "—"}</td>
        <td class="num">{_fmt_inr(sgst_amt) if sgst_amt else "—"}</td>
        <td class="num">{_fmt_inr(sale.gst_amount)}</td>
      </tr>
      <tr style="font-weight:700;background:#f7f7f7">
        <td>Total</td>
        <td class="num">{_fmt_inr(sale.subtotal)}</td>
        <td></td>
        <td class="num">{_fmt_inr(igst_amt) if igst_amt else "—"}</td>
        <td></td>
        <td class="num">{_fmt_inr(cgst_amt) if cgst_amt else "—"}</td>
        <td></td>
        <td class="num">{_fmt_inr(sgst_amt) if sgst_amt else "—"}</td>
        <td class="num">{_fmt_inr(sale.gst_amount)}</td>
      </tr>
    </tbody>
  </table>

  <!-- ══ DECLARATION + SIGNATURE ══════════════════════════════════ -->
  <div class="bottom-grid">
    <div class="decl-cell">
      <b>Declaration:</b><br/>
      We declare that this invoice shows the actual price of the goods described
      and that all particulars are true and correct. Tax is payable on Reverse
      Charge: <b>No</b>.
    </div>
    <div class="sign-cell">
      <div>for <span class="company">{SELLER['name']}</span></div>
      <div>
        <div class="sig-line">Authorised Signatory</div>
      </div>
    </div>
  </div>

  <div class="footer-note">This is a Computer Generated Invoice</div>

</body>
</html>"""
    if download:
        inv_no_safe = (sale.invoice_number or "DRAFT").replace("/", "-").replace("\\", "-")
        filename = f"Invoice-{inv_no_safe}-{sale.client_name.replace(' ', '_')}.html"
        from fastapi.responses import Response
        return Response(
            content=html,
            media_type="text/html",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    return HTMLResponse(content=html)


# ── Work Order Sale Invoice (Tax Invoice format, mirrors Sale invoice) ────────

@router.get("/wo-invoice/{sale_id}", response_class=HTMLResponse)
def get_wo_invoice_document(sale_id: int, download: bool = False, db: Session = Depends(get_db)):
    sale = db.get(WorkOrderSale, sale_id)
    if not sale:
        return _html_not_found("Work order sale not found. It may have been deleted or the link is invalid.")

    wo = db.get(WorkOrder, sale.wo_id) if sale.wo_id else None

    subtotal = sale.subtotal
    gst_amount = sale.gst_amount
    freight = sale.freight
    grand_total = sale.grand_total

    total_completed = wo.completed_qty if wo else 0
    pending_qty      = wo.pending_quantity if wo else 0
    words            = _amount_words(grand_total)

    is_intra_state = False

    gst_rate = sale.items[0].gst_rate if sale.items else 18.0
    if is_intra_state:
        igst_rate = 0.0
        igst_amt = 0.0
        cgst_rate = gst_rate / 2
        sgst_rate = gst_rate / 2
        cgst_amt = sale.gst_amount / 2
        sgst_amt = sale.gst_amount / 2
    else:
        igst_rate = gst_rate
        igst_amt = sale.gst_amount
        cgst_rate = 0.0
        cgst_amt = 0.0
        sgst_rate = 0.0
        sgst_amt = 0.0

    inv_no = sale.invoice_number or "DRAFT"
    inv_date = _fmt_date(sale.created_at)
    wo_date = _fmt_date(wo.wo_date or wo.created_at) if wo else "—"
    deliv_date = _fmt_date(sale.updated_at)
    pay_terms = (sale.payment_terms if sale.payment_terms else
                 sale.payment_note or "As per discussion")
    location = sale.ship_to or (wo.site_location if wo else "—")
    project = sale.project or "—"
    ship_to_addr = sale.ship_to or location
    bill_to_addr = sale.bill_to or sale.client_name
    dispatched_via = sale.dispatched_through or "—"
    buyer_order = sale.buyers_order_no or sale.wo_number
    hsn_sac = sale.hsn_code or "—"

    import hashlib
    irn_seed = f"{inv_no}{sale.client_name}{sale.grand_total}"
    irn_hash = hashlib.sha256(irn_seed.encode()).hexdigest()
    irn = f"{irn_hash[:32]}-{irn_hash[32:48]}"
    ack_no = str(abs(hash(irn_seed)))[:15]

    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Tax Invoice – {inv_no}</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:'Segoe UI',Arial,sans-serif;font-size:11.5px;color:#111;
          background:#fff;padding:24px 32px;max-width:900px;margin:0 auto}}

    .page-header{{
      display:grid;grid-template-columns:80px 1fr 120px;
      align-items:center;margin-bottom:6px;
    }}
    .deco-lines span{{
      display:block;height:3px;background:#333;margin-bottom:4px;
    }}
    .deco-lines span:first-child{{width:40px}}
    .deco-lines span:last-child{{width:28px}}
    .page-title{{text-align:center;font-size:17px;font-weight:700;letter-spacing:.5px}}
    .e-inv-box{{text-align:right;font-size:10px;font-weight:600;color:#444}}
    .qr-placeholder{{
      width:76px;height:76px;border:1px solid #999;
      display:flex;align-items:center;justify-content:center;
      font-size:8px;color:#888;margin-left:auto;margin-top:3px;
    }}

    .irn-bar{{font-size:10.5px;margin-bottom:8px;line-height:1.8}}
    .irn-bar .row{{display:flex;gap:6px}}
    .irn-bar .lbl{{font-weight:600;min-width:70px}}

    .main-grid{{display:grid;grid-template-columns:1fr 1fr;border:1px solid #555}}

    .seller-cell{{padding:10px 12px;font-size:11px;line-height:1.75;border-right:1px solid #555}}
    .seller-name{{font-weight:700;font-size:12px}}

    .inv-details{{font-size:10.5px}}
    .inv-details table{{width:100%;border-collapse:collapse;height:100%}}
    .inv-details td{{
      border:1px solid #555;padding:3px 7px;vertical-align:top;line-height:1.6
    }}
    .inv-details td.lbl{{font-weight:600;color:#222;background:#f7f7f7;white-space:nowrap}}
    .inv-details td.val{{font-weight:600}}
    .inv-details td.date-lbl{{font-weight:600;font-size:10px;color:#555}}

    .party-grid{{
      display:grid;grid-template-columns:1fr 1fr;
      border:1px solid #555;border-top:none;
    }}
    .party-cell{{padding:8px 12px;font-size:11px;line-height:1.75}}
    .party-cell:first-child{{border-right:1px solid #555}}
    .party-type{{font-weight:700;font-size:10.5px;text-decoration:underline;
                 margin-bottom:3px;color:#333}}
    .party-name{{font-weight:700;font-size:12px}}

    .item-table{{
      width:100%;border-collapse:collapse;
      border:1px solid #555;border-top:none;
      font-size:11px;
    }}
    .item-table th{{
      border:1px solid #555;padding:5px 7px;
      text-align:center;font-weight:700;font-size:10.5px;
      background:#f0f0f0;
    }}
    .item-table td{{
      border:1px solid #555;padding:5px 7px;
      vertical-align:top;
    }}
    .item-table td.num{{text-align:right}}
    .item-table td.ctr{{text-align:center}}

    .totals-section{{
      display:grid;grid-template-columns:1fr auto;
      border:1px solid #555;border-top:none;
    }}
    .words-cell{{
      padding:8px 12px;font-size:11px;line-height:1.6;
      border-right:1px solid #555;
    }}
    .totals-cell{{min-width:280px}}
    .totals-cell table{{width:100%;border-collapse:collapse}}
    .totals-cell td{{
      border:1px solid #555;padding:4px 10px;font-size:11px;
    }}
    .totals-cell td.lbl{{font-weight:600;background:#f7f7f7;width:160px}}
    .totals-cell td.val{{text-align:right;font-weight:600}}
    .totals-cell tr.grand td{{
      font-weight:700;font-size:12px;background:#1a1a2e;color:#fff;
    }}

    .gst-table{{
      width:100%;border-collapse:collapse;
      border:1px solid #555;border-top:none;
      font-size:10.5px;
    }}
    .gst-table th{{
      border:1px solid #555;padding:4px 7px;
      background:#f0f0f0;font-weight:700;text-align:center;
    }}
    .gst-table td{{border:1px solid #555;padding:4px 7px;text-align:center}}
    .gst-table td.num{{text-align:right}}

    .bottom-grid{{
      display:grid;grid-template-columns:1fr 1fr;
      border:1px solid #555;border-top:none;min-height:80px;
    }}
    .decl-cell{{
      padding:8px 12px;font-size:10.5px;line-height:1.7;
      border-right:1px solid #555;
    }}
    .sign-cell{{
      padding:8px 12px;font-size:10.5px;text-align:right;
      display:flex;flex-direction:column;justify-content:space-between;
    }}
    .sign-cell .company{{font-weight:700;font-size:11px}}
    .sign-cell .sig-line{{
      border-top:1px solid #555;width:160px;
      margin-left:auto;padding-top:4px;font-size:10px;
    }}

    .footer-note{{
      text-align:center;font-size:10.5px;color:#555;
      margin-top:8px;font-style:italic;
    }}

    @media print{{
      body{{padding:8px 12px}}
      @page{{size:A4;margin:8mm}}
    }}
  </style>
</head>
<body>

  <div class="page-header">
    <div class="deco-lines">
      <span></span>
      <span></span>
    </div>
    <div class="page-title">Tax Invoice</div>
    <div class="e-inv-box">
      e-Invoice
      <div class="qr-placeholder">QR Code</div>
    </div>
  </div>

  <div class="irn-bar">
    <div class="row"><span class="lbl">IRN</span><span>: {irn}</span></div>
    <div class="row"><span class="lbl">Ack No.</span><span>: {ack_no}</span></div>
    <div class="row"><span class="lbl">Ack Date</span><span>: {inv_date}</span></div>
  </div>

  <div class="main-grid">

    <div class="seller-cell">
      <div class="seller-name">{SELLER['name']} (FY{_fy_label(sale.created_at)})</div>
      <div>Regd. Office: {SELLER['address1']},</div>
      <div>{SELLER['address2']} India</div>
      <div>PLANT ADD.: VPO PALAKWAHA, TEHSIL HAROLI,</div>
      <div>Una, 177220</div>
      <div>IEC CODE NO.: 2216900613</div>
      <div>E-Mail: {SELLER['email']}</div>
    </div>

    <div class="inv-details">
      <table>
        <tr>
          <td class="lbl">Invoice No.</td>
          <td class="val">{inv_no}</td>
          <td class="lbl">e-Way Bill No.</td>
          <td class="val">{sale.e_way_bill_no or '—'}</td>
        </tr>
        <tr>
          <td class="lbl">Delivery Note</td>
          <td>—</td>
          <td class="date-lbl">Dated</td>
          <td class="val">{inv_date}</td>
        </tr>
        <tr>
          <td class="lbl">Reference No. &amp; Date.</td>
          <td colspan="3">—</td>
        </tr>
        <tr>
          <td class="lbl">Mode/Terms of Payment</td>
          <td colspan="3">{pay_terms}</td>
        </tr>
        <tr>
          <td class="lbl">Buyer's Order No.</td>
          <td class="val">{buyer_order}</td>
          <td class="date-lbl">Dated</td>
          <td class="val">{wo_date}</td>
        </tr>
        <tr>
          <td class="lbl">Dispatch Doc No.</td>
          <td>—</td>
          <td class="date-lbl">Delivery Note Date</td>
          <td class="val">{deliv_date}</td>
        </tr>
        <tr>
          <td class="lbl">Dispatched through</td>
          <td colspan="3">{dispatched_via}</td>
        </tr>
        <tr>
          <td class="lbl">Destination</td>
          <td colspan="3">{location}</td>
        </tr>
        <tr>
          <td class="lbl">Terms of Delivery</td>
          <td colspan="3">{project}</td>
        </tr>
      </table>
    </div>
  </div>

  <div class="party-grid">
    <div class="party-cell">
      <div class="party-type">Consignee (Ship to)</div>
      <div class="party-name">{sale.client_name}</div>
      <div style="white-space:pre-line">{ship_to_addr}</div>
    </div>
    <div class="party-cell">
      <div class="party-type">Buyer (Bill to)</div>
      <div class="party-name">{sale.client_name}</div>
      <div style="white-space:pre-line">{bill_to_addr}</div>
    </div>
  </div>

  <table class="item-table">
    <thead>
      <tr>
        <th style="width:30px">Sl<br/>No.</th>
        <th>Description of Goods</th>
        <th style="width:70px">HSN/SAC</th>
        <th style="width:90px">Quantity</th>
        <th style="width:80px">Rate</th>
        <th style="width:46px">per</th>
        <th style="width:52px">Disc.&nbsp;%</th>
        <th style="width:100px">Amount</th>
      </tr>
    </thead>
    <tbody>
      {''.join(
          f'''<tr>
            <td class="ctr">{i+1}</td>
            <td>
              <b>{it.item}</b>
              {('<br/><span style="font-size:10px;color:#555">' + sale.project + '</span>') if sale.project and i == 0 else ''}
            </td>
            <td class="ctr">{hsn_sac}</td>
            <td class="num">{_fmt_inr(it.quantity)}&nbsp;{it.uom or 'Nos'}</td>
            <td class="num">{_fmt_inr(it.unit_price)}</td>
            <td class="ctr">{it.uom or 'Nos'}</td>
            <td class="ctr">—</td>
            <td class="num">{_fmt_inr(it.subtotal)}</td>
          </tr>'''
          for i, it in enumerate(sale.items)
      )}
      {''.join('<tr>' + '<td style="height:20px"></td>'*8 + '</tr>' for _ in range(7))}
    </tbody>
  </table>

  <div class="totals-section">

    <div class="words-cell">
      <div style="font-weight:700;margin-bottom:4px">Amount Chargeable (in words)</div>
      <div style="font-style:italic">INR {words}</div>
      {('<div style="margin-top:8px;font-size:10.5px"><b>Payment Note:</b> ' + sale.payment_note + '</div>') if sale.payment_note else ''}
      <div style="margin-top:12px;font-size:10px;color:#666">
        Total Completed (WO): {_fmt_inr(total_completed)} &nbsp;|&nbsp;
        Balance Pending (WO): {_fmt_inr(pending_qty)}
      </div>
    </div>

    <div class="totals-cell">
      <table>
        <tr>
          <td class="lbl">Taxable Value</td>
          <td class="val">{_fmt_inr(sale.subtotal)}</td>
        </tr>
        <tr>
          <td class="lbl">IGST @ {igst_rate}%</td>
          <td class="val">{_fmt_inr(igst_amt) if igst_amt else '—'}</td>
        </tr>
        <tr>
          <td class="lbl">CGST @ {cgst_rate}%</td>
          <td class="val">{_fmt_inr(cgst_amt) if cgst_amt else '—'}</td>
        </tr>
        <tr>
          <td class="lbl">SGST @ {sgst_rate}%</td>
          <td class="val">{_fmt_inr(sgst_amt) if sgst_amt else '—'}</td>
        </tr>
        <tr>
          <td class="lbl">Freight / P&amp;F</td>
          <td class="val">{_fmt_inr(sale.freight)}</td>
        </tr>
        <tr>
          <td class="lbl">Round Off</td>
          <td class="val">—</td>
        </tr>
        <tr class="grand">
          <td class="lbl">Grand Total</td>
          <td class="val">₹&nbsp;{_fmt_inr(sale.grand_total)}</td>
        </tr>
      </table>
    </div>
  </div>

  <table class="gst-table">
    <thead>
      <tr>
        <th>HSN/SAC</th>
        <th>Taxable Value</th>
        <th colspan="2">IGST</th>
        <th colspan="2">CGST</th>
        <th colspan="2">SGST/UTGST</th>
        <th>Total Tax Amount</th>
      </tr>
      <tr>
        <th></th><th></th>
        <th>Rate</th><th>Amount</th>
        <th>Rate</th><th>Amount</th>
        <th>Rate</th><th>Amount</th>
        <th></th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td class="ctr">{hsn_sac}</td>
        <td class="num">{_fmt_inr(sale.subtotal)}</td>
        <td class="ctr">{f"{igst_rate}%" if igst_rate else "—"}</td>
        <td class="num">{_fmt_inr(igst_amt) if igst_amt else "—"}</td>
        <td class="ctr">{f"{cgst_rate}%" if cgst_rate else "—"}</td>
        <td class="num">{_fmt_inr(cgst_amt) if cgst_amt else "—"}</td>
        <td class="ctr">{f"{sgst_rate}%" if sgst_rate else "—"}</td>
        <td class="num">{_fmt_inr(sgst_amt) if sgst_amt else "—"}</td>
        <td class="num">{_fmt_inr(sale.gst_amount)}</td>
      </tr>
      <tr style="font-weight:700;background:#f7f7f7">
        <td>Total</td>
        <td class="num">{_fmt_inr(sale.subtotal)}</td>
        <td></td>
        <td class="num">{_fmt_inr(igst_amt) if igst_amt else "—"}</td>
        <td></td>
        <td class="num">{_fmt_inr(cgst_amt) if cgst_amt else "—"}</td>
        <td></td>
        <td class="num">{_fmt_inr(sgst_amt) if sgst_amt else "—"}</td>
        <td class="num">{_fmt_inr(sale.gst_amount)}</td>
      </tr>
    </tbody>
  </table>

  <div class="bottom-grid">
    <div class="decl-cell">
      <b>Declaration:</b><br/>
      We declare that this invoice shows the actual price of the goods described
      and that all particulars are true and correct. Tax is payable on Reverse
      Charge: <b>No</b>.
    </div>
    <div class="sign-cell">
      <div>for <span class="company">{SELLER['name']}</span></div>
      <div>
        <div class="sig-line">Authorised Signatory</div>
      </div>
    </div>
  </div>

  <div class="footer-note">This is a Computer Generated Invoice</div>

</body>
</html>"""
    if download:
        inv_no_safe = (sale.invoice_number or "DRAFT").replace("/", "-").replace("\\", "-")
        filename = f"Invoice-{inv_no_safe}-{sale.client_name.replace(' ', '_')}.html"
        from fastapi.responses import Response
        return Response(
            content=html,
            media_type="text/html",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    return HTMLResponse(content=html)


def _fy_label(dt) -> str:
    """Return financial year label like '2026-27' from a datetime."""
    if not dt:
        from datetime import datetime
        dt = datetime.utcnow()
    y = dt.year
    m = dt.month
    fy_start = y if m >= 4 else y - 1
    return f"{fy_start}-{str(fy_start + 1)[2:]}"


def generate_delivery_challan_html(sale, is_wo: bool = False) -> str:
    """Render an authentic, printable Delivery Challan for a Sale or WorkOrderSale."""
    challan_no = getattr(sale, "invoice_number", None) or f"DC-{sale.id}"
    challan_date = _fmt_date(getattr(sale, "invoice_date", None) or getattr(sale, "created_at", None))
    po_no = (getattr(sale, "wo_number", None) if is_wo else getattr(sale, "po_number", None)) or "—"
    client_name = getattr(sale, "client_name", None) or "—"
    project = getattr(sale, "project", None) or "—"
    dispatched_through = getattr(sale, "dispatched_through", None) or "—"
    ship_to = (getattr(sale, "ship_to", None) or client_name).replace("\n", "<br/>")
    bill_to = (getattr(sale, "bill_to", None) or client_name).replace("\n", "<br/>")
    dispatch_from = (getattr(sale, "dispatch_from", None) or f"{SELLER['name']}<br/>{SELLER['address1']}, {SELLER['address2']}").replace("\n", "<br/>")

    items_rows = ""
    total_qty = 0
    items = getattr(sale, "items", []) or []
    for idx, it in enumerate(items, 1):
        qty = float(getattr(it, "quantity", 0) or 0)
        total_qty += qty
        uom = getattr(it, "uom", "Nos")
        name = getattr(it, "item", "Item")
        items_rows += f"""
        <tr>
          <td style="text-align:center;border:1px solid #333;padding:8px 6px;">{idx}</td>
          <td style="border:1px solid #333;padding:8px 10px;font-weight:600;">{name}</td>
          <td style="text-align:center;border:1px solid #333;padding:8px 6px;">{uom}</td>
          <td style="text-align:right;border:1px solid #333;padding:8px 10px;font-weight:700;">{qty:g}</td>
          <td style="border:1px solid #333;padding:8px 6px;text-align:center;">Good Condition</td>
        </tr>
        """

    if not items_rows:
        items_rows = """<tr><td colspan="5" style="text-align:center;padding:12px;border:1px solid #333;">Dispatched Material as per Order</td></tr>"""

    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Delivery Challan – {challan_no}</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: 'Segoe UI', Arial, sans-serif;
      font-size: 12px;
      color: #111;
      background: #f8fafc;
      padding: 20px;
    }}
    .sheet {{
      background: #fff;
      max-width: 860px;
      margin: 0 auto;
      padding: 30px 36px;
      border: 1px solid #cbd5e1;
      box-shadow: 0 4px 12px rgba(0,0,0,0.08);
      border-radius: 4px;
    }}
    .print-bar {{
      max-width: 860px;
      margin: 0 auto 16px auto;
      display: flex;
      justify-content: space-between;
      align-items: center;
      background: #1e293b;
      color: #fff;
      padding: 10px 18px;
      border-radius: 6px;
    }}
    .print-btn {{
      background: #0284c7;
      color: #fff;
      border: none;
      padding: 8px 18px;
      font-weight: 600;
      border-radius: 4px;
      cursor: pointer;
      font-size: 13px;
    }}
    .print-btn:hover {{ background: #0369a1; }}
    .header-table {{
      width: 100%;
      border-collapse: collapse;
      margin-bottom: 12px;
    }}
    .doc-title {{
      text-align: center;
      font-size: 20px;
      font-weight: 800;
      letter-spacing: 1px;
      color: #0f172a;
      padding: 6px 0;
      border-top: 2px solid #0f172a;
      border-bottom: 2px solid #0f172a;
      margin: 12px 0 16px 0;
      background: #f1f5f9;
    }}
    .info-box {{
      width: 100%;
      border: 1px solid #333;
      border-collapse: collapse;
      margin-bottom: 14px;
    }}
    .info-box td {{
      border: 1px solid #333;
      padding: 8px 10px;
      vertical-align: top;
      font-size: 11.5px;
      line-height: 1.5;
    }}
    .items-table {{
      width: 100%;
      border-collapse: collapse;
      margin-top: 10px;
      margin-bottom: 16px;
    }}
    .items-table th {{
      background: #e2e8f0;
      border: 1px solid #333;
      padding: 8px 6px;
      font-size: 11.5px;
      font-weight: 700;
      text-align: center;
    }}
    .sign-box {{
      width: 100%;
      border: 1px solid #333;
      border-collapse: collapse;
      margin-top: 24px;
    }}
    .sign-box td {{
      border: 1px solid #333;
      padding: 12px;
      width: 33.33%;
      vertical-align: bottom;
      height: 90px;
      font-size: 11px;
      text-align: center;
    }}
    @media print {{
      body {{ background: #fff; padding: 0; }}
      .sheet {{ border: none; box-shadow: none; padding: 10px; }}
      .no-print {{ display: none !important; }}
    }}
  </style>
</head>
<body>
  <div class="print-bar no-print">
    <div><strong>Delivery Challan Viewer</strong> — Verified System Document</div>
    <button class="print-btn" onclick="window.print()">🖨️ Print Delivery Challan</button>
  </div>

  <div class="sheet">
    <table class="header-table">
      <tr>
        <td style="vertical-align:top;width:60%;">
          <div style="font-size:18px;font-weight:800;color:#0f172a;">{SELLER['name']}</div>
          <div style="font-size:11px;color:#475569;margin-top:3px;line-height:1.4;">
            {SELLER['address1']}<br/>
            {SELLER['address2']}<br/>
            Contact: {SELLER['contact']} | Email: {SELLER['email']}
          </div>
        </td>
        <td style="text-align:right;vertical-align:top;width:40%;">
          <div style="font-size:13px;font-weight:700;">ORIGINAL FOR RECIPIENT</div>
          <div style="font-size:11px;color:#475569;margin-top:4px;">GSTIN: 02AAFCR5621L1ZG</div>
        </td>
      </tr>
    </table>

    <div class="doc-title">DELIVERY CHALLAN</div>

    <table class="info-box">
      <tr>
        <td style="width:50%;">
          <strong>Challan / Invoice No:</strong> {challan_no}<br/>
          <strong>Challan Date:</strong> {challan_date}<br/>
          <strong>PO / WO No:</strong> {po_no}
        </td>
        <td style="width:50%;">
          <strong>Dispatched Through:</strong> {dispatched_through}<br/>
          <strong>Project:</strong> {project}
        </td>
      </tr>
      <tr>
        <td>
          <strong style="text-decoration:underline;">Consignee (Ship To):</strong><br/>
          <div style="margin-top:4px;">{ship_to}</div>
        </td>
        <td>
          <strong style="text-decoration:underline;">Buyer (Bill To):</strong><br/>
          <div style="margin-top:4px;">{bill_to}</div>
        </td>
      </tr>
    </table>

    <table class="items-table">
      <thead>
        <tr>
          <th style="width:40px;">S.No.</th>
          <th>Description of Goods / Material</th>
          <th style="width:70px;">UOM</th>
          <th style="width:100px;">Dispatched Qty</th>
          <th style="width:110px;">Remarks</th>
        </tr>
      </thead>
      <tbody>
        {items_rows}
      </tbody>
      <tfoot>
        <tr style="background:#f8fafc;font-weight:700;">
          <td colspan="3" style="text-align:right;border:1px solid #333;padding:8px 10px;">Total Dispatched Quantity:</td>
          <td style="text-align:right;border:1px solid #333;padding:8px 10px;">{total_qty:g}</td>
          <td style="border:1px solid #333;"></td>
        </tr>
      </tfoot>
    </table>

    <div style="font-size:10.5px;color:#475569;margin-top:8px;line-height:1.5;">
      <strong>Terms & Conditions:</strong><br/>
      1. Goods received in good order and condition as per specifications.<br/>
      2. Any discrepancies must be notified within 24 hours of delivery.
    </div>

    <table class="sign-box">
      <tr>
        <td>
          <div style="border-top:1px dashed #666;padding-top:4px;">Driver's Signature / Transporter</div>
        </td>
        <td>
          <div style="border-top:1px dashed #666;padding-top:4px;">Receiver's Signature & Stamp</div>
        </td>
        <td>
          <div>for <strong>{SELLER['name']}</strong></div>
          <div style="border-top:1px dashed #666;margin-top:28px;padding-top:4px;">Authorised Signatory</div>
        </td>
      </tr>
    </table>

    <div style="text-align:center;font-size:10px;color:#94a3b8;margin-top:16px;">
      This is a Verified Delivery Challan Generated by JB Engineering Corporation ERP
    </div>
  </div>
</body>
</html>"""
    return html


# ── HR Employee Profile & Document Package ──────────────────────────────────

JBEC_LOGO_DATA_URL = "data:image/jpeg;base64,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"

@router.get("/employee/{emp_id}", response_class=HTMLResponse)
def get_employee_dossier_document(emp_id: int, db: Session = Depends(get_db)):
    emp = db.get(HREmployee, emp_id)
    if not emp:
        return _html_not_found("Employee record not found. It may have been deleted or the link is invalid.")

    def fmt_d(d):
        return d.strftime("%d-%b-%Y") if d else "—"

    dob_str = fmt_d(emp.date_of_birth)
    doj_str = fmt_d(emp.date_of_joining)
    created_at_str = emp.created_at.strftime("%d-%b-%Y %I:%M %p") if emp.created_at else "—"
    updated_at_str = emp.updated_at.strftime("%d-%b-%Y %I:%M %p") if emp.updated_at else "—"

    profile_img_src = ""
    if emp.profile_image_url:
        p_url = emp.profile_image_url
        if not p_url.startswith("http") and not p_url.startswith("/"):
            p_url = "/" + p_url
        profile_img_src = f'<img src="{p_url}" alt="Profile" style="width:110px;height:130px;object-fit:cover;border:2px solid #b91c1c;border-radius:6px;" />'
    else:
        profile_img_src = '<div style="width:110px;height:130px;border:2px dashed #94a3b8;border-radius:6px;display:flex;align-items:center;justify-content:center;color:#94a3b8;font-size:11px;text-align:center;background:#f8fafc;">No Photo<br/>Uploaded</div>'

    docs = []
    if emp.aadhaar_doc_url:
        docs.append(("Aadhaar Card", f"Aadhaar Number: {emp.aadhaar_number or '—'}", emp.aadhaar_doc_url))
    if emp.pan_doc_url:
        docs.append(("PAN Card", f"PAN Number: {emp.pan_number or '—'}", emp.pan_doc_url))
    if emp.bank_doc_url:
        docs.append(("Bank Proof Document", f"{emp.bank_name or 'Bank'} (A/C: {emp.bank_account_number or '—'}, IFSC: {emp.bank_ifsc or '—'})", emp.bank_doc_url))
    if emp.qualification_doc_url:
        docs.append(("Educational Qualification Certificate", emp.qualification or "Degree / Diploma Certificate", emp.qualification_doc_url))

    doc_sections_html = ""
    for idx, (title, subtitle, url) in enumerate(docs, 1):
        doc_url = url
        if not doc_url.startswith("http") and not doc_url.startswith("/"):
            doc_url = "/" + doc_url
        is_pdf = doc_url.lower().endswith(".pdf")

        if is_pdf:
            doc_sections_html += f"""
            <div class="doc-card page-break" id="pdf-doc-{idx}">
              <div class="doc-header">
                <span>ATTACHED DOCUMENT {idx}: {title.upper()}</span>
                <span>Emp: {emp.name} ({emp.employee_code})</span>
              </div>
              <div class="doc-sub">
                <span>Details: <strong>{subtitle}</strong></span>
                <a href="{doc_url}" target="_blank" class="open-original-link">↗ Open Original PDF</a>
              </div>
              <div class="doc-body pdf-canvas-container" id="pdf-container-{idx}" data-pdf-url="{doc_url}">
                <div class="pdf-loading" style="padding: 14px; font-size: 11px; color: #64748b; text-align: center;">
                  📄 Loading document pages for print/view...
                </div>
                <iframe src="{doc_url}" class="pdf-fallback-iframe" style="width:100%;height:850px;border:none;border-radius:4px;"></iframe>
              </div>
            </div>
            """
        else:
            doc_sections_html += f"""
            <div class="doc-card page-break">
              <div class="doc-header">
                <span>ATTACHED DOCUMENT {idx}: {title.upper()}</span>
                <span>Emp: {emp.name} ({emp.employee_code})</span>
              </div>
              <div class="doc-sub">
                <span>Details: <strong>{subtitle}</strong></span>
                <a href="{doc_url}" target="_blank" class="open-original-link">↗ Open Full Size Image</a>
              </div>
              <div class="doc-body">
                <img src="{doc_url}" alt="{title}" class="doc-img" />
              </div>
            </div>
            """

    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Employee Profile – {emp.employee_code} – {emp.name}</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: 'Segoe UI', Arial, sans-serif;
      font-size: 12px;
      color: #1e293b;
      background: #f1f5f9;
      padding: 20px;
    }}
    .print-bar {{
      max-width: 900px;
      margin: 0 auto 16px;
      background: #0f172a;
      color: #fff;
      padding: 10px 18px;
      border-radius: 8px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1);
    }}
    .print-btn {{
      background: #b91c1c;
      color: #fff;
      border: none;
      padding: 8px 18px;
      border-radius: 6px;
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 6px;
    }}
    .print-btn:hover {{ background: #991b1b; }}
    .container {{
      max-width: 900px;
      margin: 0 auto;
      background: #fff;
      padding: 28px 34px;
      border-radius: 8px;
      box-shadow: 0 4px 12px rgba(0,0,0,0.06);
    }}
    .header-table {{
      width: 100%;
      border-bottom: 2px solid #b91c1c;
      padding-bottom: 12px;
      margin-bottom: 16px;
    }}
    .header-table td {{ vertical-align: top; }}
    .company-name {{
      font-size: 21px;
      font-weight: 800;
      color: #a81c1c;
      letter-spacing: 0.5px;
    }}
    .company-sub {{
      font-size: 11px;
      color: #64748b;
      margin-top: 3px;
    }}
    .doc-badge {{
      display: inline-block;
      background: #fef2f2;
      color: #b91c1c;
      border: 1px solid #fecaca;
      padding: 4px 10px;
      border-radius: 4px;
      font-weight: 700;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 1px;
      margin-top: 6px;
    }}
    .section-title {{
      font-size: 12px;
      font-weight: 700;
      color: #991b1b;
      text-transform: uppercase;
      letter-spacing: 0.6px;
      background: #fef2f2;
      border-left: 3px solid #b91c1c;
      padding: 5px 10px;
      margin-top: 14px;
      margin-bottom: 8px;
    }}
    .info-table {{
      width: 100%;
      border-collapse: collapse;
      margin-bottom: 6px;
    }}
    .info-table th, .info-table td {{
      padding: 6px 10px;
      font-size: 11.5px;
      border: 1px solid #e2e8f0;
      vertical-align: middle;
    }}
    .info-table th {{
      background: #f8fafc;
      width: 25%;
      color: #475569;
      font-weight: 600;
      text-align: left;
    }}
    .info-table td {{
      width: 25%;
      color: #0f172a;
    }}
    .sign-table {{
      width: 100%;
      margin-top: 24px;
      border-top: 1px dashed #cbd5e1;
      padding-top: 12px;
    }}
    .sign-table td {{
      vertical-align: bottom;
      padding: 10px;
      text-align: center;
      font-size: 11px;
      color: #475569;
    }}
    .sign-line {{
      border-top: 1px solid #475569;
      width: 160px;
      margin: 30px auto 4px;
    }}
    .page-break {{
      page-break-before: always;
      break-before: page;
    }}
    .doc-card {{
      margin-top: 24px;
      border: 1px solid #cbd5e1;
      border-radius: 8px;
      background: #fff;
      overflow: hidden;
      box-shadow: 0 2px 8px rgba(0,0,0,0.04);
    }}
    .doc-header {{
      background: #991b1b;
      color: #fff;
      padding: 10px 16px;
      font-weight: 700;
      font-size: 12px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }}
    .doc-sub {{
      font-size: 11px;
      color: #475569;
      background: #f8fafc;
      padding: 8px 16px;
      border-bottom: 1px solid #e2e8f0;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }}
    .open-original-link {{
      color: #b91c1c;
      text-decoration: none;
      font-weight: 600;
      font-size: 11px;
    }}
    .open-original-link:hover {{
      text-decoration: underline;
    }}
    .doc-body {{
      padding: 20px;
      text-align: center;
      background: #f8fafc;
      min-height: 250px;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
    }}
    .doc-img {{
      max-width: 100%;
      max-height: 850px;
      object-fit: contain;
      border-radius: 6px;
      box-shadow: 0 2px 6px rgba(0,0,0,0.08);
      background: #fff;
    }}
    .pdf-page-canvas {{
      max-width: 100%;
      height: auto;
      border-radius: 4px;
      background: #fff;
    }}
    @media print {{
      body {{ background: #fff; padding: 0; margin: 0; }}
      .print-bar {{ display: none !important; }}
      .open-original-link {{ display: none !important; }}
      .container {{ box-shadow: none; padding: 0; max-width: 100%; border: none; }}
      .doc-card {{ border: 1px solid #94a3b8; margin-top: 20px; border-radius: 4px; box-shadow: none; }}
      .doc-body {{ background: #fff; padding: 8px 0; }}
      .pdf-fallback-iframe {{ display: none !important; }}
      .pdf-loading {{ display: none !important; }}
      @page {{ size: A4 portrait; margin: 10mm 12mm; }}
    }}
  </style>
</head>
<body>
  <div class="print-bar">
    <div>
      <strong>JB Engineering HR Department</strong> — Employee Profile & KYC Documents
    </div>
    <button class="print-btn" onclick="window.print()">🖨️ Print / Save as PDF</button>
  </div>

  <div class="container">
    <table class="header-table">
      <tr>
        <td style="width: 75%; vertical-align: middle;">
          <div style="display: flex; align-items: center; gap: 14px;">
            <img src="{JBEC_LOGO_DATA_URL}" alt="JB Engineering" style="height: 54px; max-width: 90px; object-fit: contain; border-radius: 4px; flex-shrink: 0;" />
            <div>
              <div class="company-name">RE-Bar Couplers India Private Limited</div>
              <div class="company-sub">Corporate HR Department &bull; hrd@jbengineeringcorporation.com</div>
              <div class="doc-badge">EMPLOYEE BIO-DATA & PROFILE FORM</div>
            </div>
          </div>
        </td>
        <td style="width: 25%; text-align: right; vertical-align: middle;">
          {profile_img_src}
        </td>
      </tr>
    </table>

    <!-- Section 1: Basic & Employment Profile -->
    <div class="section-title">1. Employee & Job Details</div>
    <table class="info-table">
      <tr>
        <th>Employee Code</th>
        <td style="font-weight: 700; color: #b91c1c;">{emp.employee_code}</td>
        <th>Full Name</th>
        <td style="font-weight: 700;">{emp.name}</td>
      </tr>
      <tr>
        <th>Designation</th>
        <td>{emp.designation or '—'}</td>
        <th>Location</th>
        <td>{emp.location or '—'}</td>
      </tr>
      <tr>
        <th>Date of Joining</th>
        <td>{doj_str}</td>
        <th>Employment Status</th>
        <td style="font-weight: 700; color: {'#16a34a' if emp.is_active else '#dc2626'};">
          {'ACTIVE' if emp.is_active else f'INACTIVE ({fmt_d(emp.inactive_date)})'}
        </td>
      </tr>
      <tr>
        <th>Starting Salary</th>
        <td>₹{_fmt_inr(emp.starting_salary)}</td>
        <th>Present Salary</th>
        <td style="font-weight: 700; color: #b91c1c;">₹{_fmt_inr(emp.present_salary)}</td>
      </tr>
    </table>

    <!-- Section 2: Personal Details -->
    <div class="section-title">2. Personal & Contact Details</div>
    <table class="info-table">
      <tr>
        <th>Father's Name</th>
        <td>{emp.father_name or '—'}</td>
        <th>Date of Birth</th>
        <td>{dob_str}</td>
      </tr>
      <tr>
        <th>Gender</th>
        <td>{emp.gender or '—'}</td>
        <th>Marital Status</th>
        <td>{emp.marital_status or '—'}</td>
      </tr>
      <tr>
        <th>Mobile Number</th>
        <td>{emp.mobile or '—'}</td>
        <th>Email ID</th>
        <td>{emp.email or '—'}</td>
      </tr>
      <tr>
        <th>Highest Qualification</th>
        <td colspan="3">{emp.qualification or '—'}</td>
      </tr>
      <tr>
        <th>Residential Address</th>
        <td colspan="3">{emp.address or '—'}</td>
      </tr>
    </table>

    <!-- Section 3: Banking & Payroll -->
    <div class="section-title">3. Bank Account & Salary Details</div>
    <table class="info-table">
      <tr>
        <th>Account Holder Name</th>
        <td>{emp.bank_account_holder or '—'}</td>
        <th>Bank Name</th>
        <td>{emp.bank_name or '—'}</td>
      </tr>
      <tr>
        <th>Account Number</th>
        <td style="font-family: monospace; font-weight: 700;">{emp.bank_account_number or '—'}</td>
        <th>IFSC Code</th>
        <td style="font-family: monospace; font-weight: 700;">{emp.bank_ifsc or '—'}</td>
      </tr>
      <tr>
        <th>Bank Branch</th>
        <td colspan="3">{emp.bank_branch or '—'}</td>
      </tr>
    </table>

    <!-- Section 4: Identity & KYC Numbers -->
    <div class="section-title">4. Government ID & KYC Details (Aadhaar / PAN)</div>
    <table class="info-table">
      <tr>
        <th>Aadhaar Number</th>
        <td style="font-family: monospace; font-weight: 700;">{emp.aadhaar_number or '—'}</td>
        <th>PAN Number</th>
        <td style="font-family: monospace; font-weight: 700;">{emp.pan_number or '—'}</td>
      </tr>
      <tr>
        <th>Documents Attached</th>
        <td colspan="3">
          {len(docs)} Document(s) Attached: {', '.join(t[0] for t in docs) if docs else 'None uploaded'}
        </td>
      </tr>
    </table>

    <!-- Section 5: Audit & Signatures -->
    <div class="section-title">5. Verification & Signatures</div>
    <table class="info-table">
      <tr>
        <th>Created By</th>
        <td>{emp.created_by or 'System'} on {created_at_str}</td>
        <th>Last Updated By</th>
        <td>{emp.updated_by or emp.created_by or '—'} on {updated_at_str}</td>
      </tr>
    </table>

    <table class="sign-table">
      <tr>
        <td>
          <div class="sign-line"></div>
          <div>Employee Signature</div>
        </td>
        <td>
          <div class="sign-line"></div>
          <div>HR Executive / Verified By</div>
        </td>
        <td>
          <div class="sign-line"></div>
          <div>Authorized Signatory / Director</div>
        </td>
      </tr>
    </table>

    <!-- Attached Documents Section (Shown below the form) -->
    {doc_sections_html}

    <div style="text-align: center; font-size: 10px; color: #94a3b8; margin-top: 24px;">
      This is a system-generated verified employee profile document from JB Engineering HR Department
    </div>
  </div>

  <script src="https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js"></script>
  <script>
    document.addEventListener("DOMContentLoaded", function() {{
      if (window.pdfjsLib) {{
        pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';
        const containers = document.querySelectorAll('.pdf-canvas-container');
        containers.forEach(async (container) => {{
          const url = container.getAttribute('data-pdf-url');
          if (!url) return;
          try {{
            const loadingTask = pdfjsLib.getDocument(url);
            const pdf = await loadingTask.promise;
            container.innerHTML = '';
            for (let pageNum = 1; pageNum <= pdf.numPages; pageNum++) {{
              const page = await pdf.getPage(pageNum);
              const viewport = page.getViewport({{ scale: 1.8 }});
              const canvas = document.createElement('canvas');
              canvas.className = 'pdf-page-canvas';
              canvas.height = viewport.height;
              canvas.width = viewport.width;
              canvas.style.width = '100%';
              canvas.style.maxWidth = '850px';
              canvas.style.height = 'auto';
              canvas.style.display = 'block';
              canvas.style.margin = '0 auto 16px';
              canvas.style.boxShadow = '0 1px 4px rgba(0,0,0,0.1)';
              const ctx = canvas.getContext('2d');
              await page.render({{ canvasContext: ctx, viewport: viewport }}).promise;
              container.appendChild(canvas);
            }}
          }} catch (err) {{
            console.warn("Could not render PDF via PDF.js, using iframe fallback:", err);
          }}
        }});
      }}
    }});
  </script>
</body>
</html>"""
    return html
