import json
import datetime
import decimal
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.database import get_db
from app.routers.sales import recalc_po_delivered_quantities
from app.routers.work_order_sales import recalc_wo_completed_quantities

# Import all models
from app.models.models import (
    User, ItemMasterItem, ItemMasterSize, WOItemMasterItem, WOItemMasterSize,
    UOMOption, Client, Project, Product, PurchaseOrder, POLineItem,
    Sale, SaleItem, SaleActivity, SaleDispatch, SaleDispatchItem,
    Record, SystemLog, WorkOrder, WOLineItem, WorkOrderSale,
    WorkOrderSaleItem, WorkOrderSaleActivity, WorkOrderSaleDispatch,
    WorkOrderSaleDispatchItem, UserSession, CompanyAddress
)

router = APIRouter(prefix="/api/system", tags=["System"])

# List of all models in dependency order (parents first for insert, children first for delete)
# Note: For export, order doesn't matter much. For import, we use SET FOREIGN_KEY_CHECKS=0 
# but it's good practice to have them ordered.
MODELS = [
    User,
    CompanyAddress,
    Client,
    Project,
    UOMOption,
    ItemMasterItem,
    ItemMasterSize,
    WOItemMasterItem,
    WOItemMasterSize,
    Product,
    PurchaseOrder,
    POLineItem,
    WorkOrder,
    WOLineItem,
    Sale,
    SaleItem,
    SaleActivity,
    SaleDispatch,
    SaleDispatchItem,
    WorkOrderSale,
    WorkOrderSaleItem,
    WorkOrderSaleActivity,
    WorkOrderSaleDispatch,
    WorkOrderSaleDispatchItem,
    Record,
    SystemLog,
    UserSession
]

class JSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (datetime.datetime, datetime.date)):
            return obj.isoformat()
        if isinstance(obj, decimal.Decimal):
            return str(obj)
        # Handle enums
        if hasattr(obj, "value"):
            return obj.value
        return super().default(obj)

@router.get("/backup/export")
def export_database(db: Session = Depends(get_db)):
    """
    Export the complete database to a JSON structure.
    Returns all records for all models.
    """
    backup_data = {}
    for model in MODELS:
        table_name = model.__tablename__
        records = db.query(model).all()
        # Convert to dict, removing SQLAlchemy internal state
        serialized_records = []
        for r in records:
            r_dict = {}
            for c in model.__table__.columns:
                r_dict[c.name] = getattr(r, c.name)
            serialized_records.append(r_dict)
        backup_data[table_name] = serialized_records

    from fastapi import Response
    json_str = json.dumps(backup_data, cls=JSONEncoder)
    
    filename = f"database_backup_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    
    return Response(
        content=json_str,
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

import os

@router.post("/backup/import")
async def import_database(
    file: UploadFile = File(...),
    import_type: str = Form("merge"),
    db: Session = Depends(get_db)
):
    """
    Import a complete database JSON dump.
    Performs a safe, non-destructive merge using ID remapping.
    """
    if not file.filename.endswith(".json"):
        raise HTTPException(status_code=400, detail="Only JSON files are allowed.")
    
    try:
        content = await file.read()
        backup_data = json.loads(content)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON file: {str(e)}")

    try:
        # 1. Create a Safety Backup before proceeding
        backup_dir = "uploads/backups"
        os.makedirs(backup_dir, exist_ok=True)
        safety_filename = f"{backup_dir}/safety_backup_pre_import_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        safety_data = {}
        for model in MODELS:
            table_name = model.__tablename__
            records = db.query(model).all()
            serialized = []
            for r in records:
                r_dict = {}
                for c in model.__table__.columns:
                    r_dict[c.name] = getattr(r, c.name)
                serialized.append(r_dict)
            safety_data[table_name] = serialized

        with open(safety_filename, "w") as f:
            json.dump(safety_data, f, cls=JSONEncoder)

        # 2. Parse Dates for the imported data
        for table_name, records in backup_data.items():
            for record in records:
                for key, value in record.items():
                    if isinstance(value, str):
                        try:
                            if len(value) == 10 and value.count("-") == 2:
                                record[key] = datetime.datetime.strptime(value, "%Y-%m-%d").date()
                            elif "T" in value and (len(value) == 19 or "." in value):
                                record[key] = datetime.datetime.fromisoformat(value)
                        except (ValueError, TypeError):
                            pass

        # 3. ID Remapping and Deduplication
        id_map = {model.__tablename__: {} for model in MODELS}

        UNIQUE_KEYS = {
            'users': ['email'],
            'company_addresses': ['title'],
            'clients': ['name'],
            'projects': ['name', 'client_id'],
            'uom_options': ['name'],
            'item_master': ['name'],
            'item_master_sizes': ['item_id', 'size'],
            'wo_item_master': ['name'],
            'wo_item_master_sizes': ['item_id', 'size'],
            'products': ['name'],
            'purchase_orders': ['po_number'],
            'po_line_items': ['po_id', 'item', 'quantity', 'unit_price'],
            'work_orders': ['wo_number'],
            'wo_line_items': ['wo_id', 'item', 'quantity'],
            'sales': ['invoice_number'],
            'sale_items': ['sale_id', 'item', 'quantity', 'unit_price'],
            'work_order_sales': ['invoice_number'],
            'work_order_sale_items': ['sale_id', 'item', 'quantity', 'unit_price'],
            'records': ['invoice_number', 'po_number'],
            'system_logs': None,
            'sale_activities': ['sale_id', 'action', 'at'],
            'work_order_sale_activities': ['sale_id', 'action', 'at'],
            'sale_dispatches': ['sale_id', 'dispatched_at'],
            'sale_dispatch_items': ['dispatch_id', 'item'],
            'work_order_sale_dispatches': ['sale_id', 'dispatched_at'],
            'work_order_sale_dispatch_items': ['dispatch_id', 'item'],
        }

        records_inserted = 0
        records_skipped = 0
        
        # If replace mode, clear database first in correct dependency order
        if import_type == "replace":
            for model in reversed(MODELS):
                db.query(model).delete()
            db.commit()
            
        # Track IDs of newly inserted records so their children bypass deduplication
        inserted_parent_ids = {model.__tablename__: set() for model in MODELS}
        
        # Track which existing DB records we've already matched in this import session
        # This prevents two identical backup rows from mapping to the SAME single DB row,
        # which correctly restores perfectly identical line items that were dropped previously.
        matched_db_ids = set()

        for model in MODELS:
            table_name = model.__tablename__
            records = backup_data.get(table_name, [])
            unique_cols = UNIQUE_KEYS.get(table_name)
            
            # Introspect foreign keys for this model
            fks = {}
            for c in model.__table__.columns:
                if c.foreign_keys:
                    for fk in c.foreign_keys:
                        fks[c.name] = fk.column.table.name

            for row in records:
                old_id = row.get('id')
                
                # A. Translate Foreign Keys
                if import_type == "merge":
                    for fk_col, target_table in fks.items():
                        old_fk_val = row.get(fk_col)
                        if old_fk_val is not None:
                            if target_table in id_map and old_fk_val in id_map[target_table]:
                                row[fk_col] = id_map[target_table][old_fk_val]

                # Only run deduplication logic if we are merging
                existing = None
                belongs_to_new_parent = False
                
                if import_type == "merge":
                    # Check if this record belongs to a parent that was JUST inserted
                    for fk_col, target_table in fks.items():
                        val = row.get(fk_col)
                        if val is not None and target_table in inserted_parent_ids and val in inserted_parent_ids[target_table]:
                            belongs_to_new_parent = True
                            break

                    # If the parent was just inserted, we don't deduplicate! We know it's a fresh child.
                    # This perfectly preserves identical line items in a fresh invoice.
                    if unique_cols and not belongs_to_new_parent:
                        filters = {}
                        for col in unique_cols:
                            filters[col] = row.get(col)
                        
                        # Only query if we have at least one valid key value
                        if any(v is not None for v in filters.values()):
                            matching_records = db.query(model).filter_by(**filters).all()
                            for rec in matching_records:
                                # Create a unique tracking key for this table's ID
                                match_key = f"{table_name}_{rec.id}"
                                if match_key not in matched_db_ids:
                                    existing = rec
                                    matched_db_ids.add(match_key)
                                    break

                        
                if existing:
                    if old_id:
                        id_map[table_name][old_id] = existing.id
                    records_skipped += 1
                    continue # Skip duplicate
                    
                # In merge mode, strip ID so it gets a new one. In replace mode, KEEP IT.
                if old_id and import_type == "merge":
                    del row['id']
                    
                new_instance = model(**row)
                db.add(new_instance)
                db.flush()
                
                inserted_parent_ids[table_name].add(new_instance.id)
                
                if old_id and import_type == "merge":
                    id_map[table_name][old_id] = new_instance.id
                records_inserted += 1
                    
        db.commit()

        # D. Post-Import: Recalculate all cached quantities to guarantee dashboard accuracy
        for po in db.query(PurchaseOrder).all():
            recalc_po_delivered_quantities(db, po)
        for wo in db.query(WorkOrder).all():
            recalc_wo_completed_quantities(db, wo)
        db.commit()

        
        return {
            "detail": "Database safely merged.",
            "records_inserted": records_inserted,
            "records_skipped": records_skipped
        }
    
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Merge failed, no changes made. Error: {str(e)}")
