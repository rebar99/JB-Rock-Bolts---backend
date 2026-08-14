import json
import datetime
import decimal
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.database import get_db

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
            'company_addresses': ['name'],
            'clients': ['name'],
            'projects': ['name', 'client_id'],
            'uom_options': ['name'],
            'item_master': ['name'],
            'item_master_sizes': ['item_id', 'size'],
            'wo_item_master': ['name'],
            'wo_item_master_sizes': ['item_id', 'size'],
            'products': ['name'],
            'purchase_orders': ['po_number'],
            'po_line_items': ['po_id', 'item'],
            'work_orders': ['wo_number'],
            'wo_line_items': ['wo_id', 'item'],
            'sales': ['invoice_number'],
            'sale_items': ['sale_id', 'item'],
            'work_order_sales': ['invoice_number'],
            'work_order_sale_items': ['sale_id', 'item'],
            'records': ['grn_number'],
            'system_logs': ['timestamp', 'action', 'entity_name'],
            'sale_activities': ['sale_id', 'action', 'at'],
            'work_order_sale_activities': ['sale_id', 'action', 'at'],
            'sale_dispatches': ['sale_id', 'dispatched_at'],
            'sale_dispatch_items': ['dispatch_id', 'item'],
            'work_order_sale_dispatches': ['sale_id', 'dispatched_at'],
            'work_order_sale_dispatch_items': ['dispatch_id', 'item'],
        }

        records_inserted = 0
        records_skipped = 0

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
                for fk_col, target_table in fks.items():
                    old_fk_val = row.get(fk_col)
                    if old_fk_val is not None:
                        if target_table in id_map and old_fk_val in id_map[target_table]:
                            row[fk_col] = id_map[target_table][old_fk_val]

                # B. Deduplication Check
                existing = None
                if unique_cols:
                    filters = {}
                    for col in unique_cols:
                        filters[col] = row.get(col)
                    
                    # Only query if we have at least one valid key value
                    if any(v is not None for v in filters.values()):
                        existing = db.query(model).filter_by(**filters).first()
                        
                if existing:
                    if old_id:
                        id_map[table_name][old_id] = existing.id
                    records_skipped += 1
                    continue # Skip duplicate
                    
                # C. Insert New Record
                if 'id' in row:
                    del row['id']
                    
                new_instance = model(**row)
                db.add(new_instance)
                db.flush()
                
                if old_id:
                    id_map[table_name][old_id] = new_instance.id
                records_inserted += 1
                    
        db.commit()
        
        return {
            "detail": "Database safely merged.",
            "records_inserted": records_inserted,
            "records_skipped": records_skipped
        }
    
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Merge failed, no changes made. Error: {str(e)}")
