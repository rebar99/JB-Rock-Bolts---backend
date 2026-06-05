import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from app.database import SessionLocal
from app.models.models import Sale, SaleItem

def fix_sale_totals():
    db = SessionLocal()
    try:
        sales = db.query(Sale).all()
        print(f"Checking {len(sales)} sales...")
        
        for sale in sales:
            # Force refresh items
            items = db.query(SaleItem).filter(SaleItem.sale_id == sale.id).all()
            if not items:
                continue
                
            calc_subtotal = sum(it.subtotal for it in items)
            calc_gst = sum(it.gst_amount for it in items)
            freight = sale.freight or 0
            calc_grand = calc_subtotal + calc_gst + freight
            
            print(f"Sale ID {sale.id} ({sale.invoice_number}): Current Grand={sale.grand_total}, Calculated={calc_grand}")
            
            # Update values
            sale.subtotal = calc_subtotal
            sale.gst_amount = calc_gst
            sale.grand_total = calc_grand
            
        db.commit()
        print("Commit complete.")
        
        # Verify immediately
        db.expire_all()
        for sale in db.query(Sale).all():
             print(f"Verified Sale {sale.id}: {sale.grand_total}")
             
    except Exception as e:
        print(f"Error: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    fix_sale_totals()
