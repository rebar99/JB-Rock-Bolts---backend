from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.models import PurchaseOrder, Sale

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


# ── PO Document ───────────────────────────────────────────────────────────────

@router.get("/po/{po_id}", response_class=HTMLResponse)
def get_po_document(po_id: int, db: Session = Depends(get_db)):
    po = db.get(PurchaseOrder, po_id)
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found.")

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


# ── Sales Invoice (Tax Invoice format) ────────────────────────────────────────

@router.get("/invoice/{sale_id}", response_class=HTMLResponse)
def get_invoice_document(sale_id: int, download: bool = False, db: Session = Depends(get_db)):
    sale = db.get(Sale, sale_id)
    if not sale:
        raise HTTPException(status_code=404, detail="Sale not found.")

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


def _fy_label(dt) -> str:
    """Return financial year label like '2026-27' from a datetime."""
    if not dt:
        from datetime import datetime
        dt = datetime.utcnow()
    y = dt.year
    m = dt.month
    fy_start = y if m >= 4 else y - 1
    return f"{fy_start}-{str(fy_start + 1)[2:]}"
