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
    User, Application, ApplicationAccess, ItemMasterItem, ItemMasterSize, WOItemMasterItem, WOItemMasterSize,
    UOMOption, Client, Project, Product, PurchaseOrder, POLineItem,
    Sale, SaleItem, SaleActivity, SaleDispatch, SaleDispatchItem,
    Record, SystemLog, WorkOrder, WOLineItem, WorkOrderSale,
    WorkOrderSaleItem, WorkOrderSaleActivity, WorkOrderSaleDispatch,
    WorkOrderSaleDispatchItem, CreditNote, CreditNoteItem, UserSession, CompanyAddress,
    POApprovalLevel, POApprovalLevelApprover, POApproverPermission,
    StoreVendor, StorePOItemMasterItem, StorePOItemMasterSize, StoreUOMOption,
    StorePurchaseItem, StorePurchaseStockItem, StorePurchaseStockTransaction,
    StorePurchaseStockTransfer, StorePurchaseOrder, StorePurchaseOrderDetail,
    StorePurchaseOrderLine, StorePurchaseOrderApproval, StorePurchaseOrderActivity,
    StorePurchaseMaterialReceipt, StorePurchaseMaterialReceiptLine
)

router = APIRouter(prefix="/api/system", tags=["System"])

# List of all models in dependency order (parents first for insert, children first for delete)
# Note: For export, order doesn't matter much. For import, we use SET FOREIGN_KEY_CHECKS=0 
# but it's good practice to have them ordered.
STORE_MODELS = [
    StoreVendor,
    StoreUOMOption,
    StorePOItemMasterItem,
    StorePOItemMasterSize,
    StorePurchaseItem,
    StorePurchaseStockItem,
    StorePurchaseOrder,
    StorePurchaseOrderDetail,
    StorePurchaseOrderLine,
    StorePurchaseOrderApproval,
    StorePurchaseOrderActivity,
    StorePurchaseMaterialReceipt,
    StorePurchaseMaterialReceiptLine,
    StorePurchaseStockTransaction,
    StorePurchaseStockTransfer,
]

MARKETING_MODELS = [
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
    CreditNote,
    CreditNoteItem,
    POApprovalLevel,
    POApprovalLevelApprover,
    POApproverPermission,
    Record,
]

MODELS = [
    Application,
    User,
    ApplicationAccess,
    *MARKETING_MODELS,
    *STORE_MODELS,
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
def export_database(
    scope: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    Export database records to a JSON structure filtered by scope.
    scope: 'store' | 'marketing' | 'all'
    """
    scope_val = (scope or "all").strip().lower()
    if scope_val == "store":
        selected_models = STORE_MODELS
        prefix = "store_backup"
    elif scope_val == "marketing":
        selected_models = MARKETING_MODELS
        prefix = "marketing_backup"
    else:
        selected_models = MODELS
        prefix = "database_backup"

    backup_data = {
        "__metadata__": {
            "scope": scope_val,
            "exported_at": datetime.datetime.utcnow().isoformat(),
            "version": "2.0"
        }
    }
    for model in selected_models:
        table_name = model.__tablename__
        records = db.query(model).all()
        serialized_records = []
        for r in records:
            r_dict = {}
            for c in model.__table__.columns:
                r_dict[c.name] = getattr(r, c.name)
            serialized_records.append(r_dict)
        backup_data[table_name] = serialized_records

    from fastapi import Response
    json_str = json.dumps(backup_data, cls=JSONEncoder)
    
    filename = f"{prefix}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    
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
    scope: Optional[str] = Form(None),
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
            if table_name.startswith("__") or not isinstance(records, list):
                continue
            for record in records:
                if not isinstance(record, dict):
                    continue
                for key, value in record.items():
                    if isinstance(value, str):
                        try:
                            if len(value) == 10 and value.count("-") == 2:
                                record[key] = datetime.datetime.strptime(value, "%Y-%m-%d").date()
                            elif "T" in value and (len(value) >= 19 or "." in value):
                                record[key] = datetime.datetime.fromisoformat(value.replace("Z", "+00:00") if value.endswith("Z") else value)
                        except (ValueError, TypeError):
                            pass

        # 3. ID Remapping and Deduplication
        id_map = {model.__tablename__: {} for model in MODELS}

        UNIQUE_KEYS = {
            'users': ['email'],
            'applications': ['code'],
            'application_access': ['user_id', 'application_id'],
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
            'credit_notes': ['cn_number'],
            'credit_note_items': ['credit_note_id', 'item', 'credit_qty', 'unit_price'],
            'po_approval_levels': ['name'],
            'po_approval_level_approvers': ['level_id', 'user_id'],
            'po_approver_permissions': ['user_id'],
            'store_vendors': ['vendor_name'],
            'store_po_item_master': ['name'],
            'store_po_item_master_sizes': ['item_id', 'size'],
            'store_uom_options': ['name'],
            'store_purchase_items': ['name'],
            'store_purchase_stock_items': ['material_id', 'name'],
            'store_purchase_orders': ['order_number'],
            'store_purchase_order_details': ['order_id'],
            'store_purchase_order_lines': ['order_id', 'line_number'],
            'store_purchase_order_approvals': ['order_id', 'level_id', 'approver_id'],
            'store_purchase_order_activities': ['order_id', 'action', 'created_at'],
            'store_purchase_material_receipts': ['order_id', 'invoice_number'],
            'store_purchase_material_receipt_lines': ['receipt_id', 'line_number'],
            'store_purchase_stock_transactions': ['receipt_line_id'],
            'store_purchase_stock_transfers': ['item_id', 'quantity', 'from_location', 'to_location', 'transfer_date'],
        }

        records_inserted = 0
        records_skipped = 0
        
        # Detect scope (from form or metadata or content)
        file_scope = backup_data.get("__metadata__", {}).get("scope")
        effective_scope = (scope or file_scope or "").strip().lower()
        if not effective_scope:
            # Auto-detect from tables in backup
            has_store = any(m.__tablename__ in backup_data for m in STORE_MODELS)
            has_marketing = any(m.__tablename__ in backup_data for m in MARKETING_MODELS)
            if has_store and not has_marketing:
                effective_scope = "store"
            elif has_marketing and not has_store:
                effective_scope = "marketing"
            else:
                effective_scope = "all"

        if effective_scope == "store":
            selected_models = STORE_MODELS
        elif effective_scope == "marketing":
            selected_models = MARKETING_MODELS
        else:
            selected_models = MODELS

        # Temporarily disable foreign key checks for clean bulk replacement/merging
        try:
            db.execute(text("SET FOREIGN_KEY_CHECKS = 0;"))
        except Exception:
            pass

        # If replace mode, clear database first in correct dependency order for the scoped models
        if import_type == "replace":
            if effective_scope == "store":
                # Only clear Store tables - Marketing & Users remain 100% untouched!
                for model in reversed(STORE_MODELS):
                    db.query(model).delete(synchronize_session=False)
                db.commit()
            elif effective_scope == "marketing":
                # Only clear Marketing tables - Store & Users remain 100% untouched!
                for model in reversed(MARKETING_MODELS):
                    db.query(model).delete(synchronize_session=False)
                db.commit()
            else:
                for model in reversed(MODELS):
                    if model in (ApplicationAccess, UserSession):
                        continue
                    if model.__tablename__ == "applications" and "applications" not in backup_data:
                        continue
                    db.query(model).delete(synchronize_session=False)
                db.commit()
            
        # Track IDs of newly inserted records so their children bypass deduplication
        inserted_parent_ids = {model.__tablename__: set() for model in selected_models}
        
        # Track which existing DB records we've already matched in this import session
        matched_db_ids = set()

        for model in selected_models:
            table_name = model.__tablename__
            records = backup_data.get(table_name, [])
            if not isinstance(records, list):
                continue
            unique_cols = UNIQUE_KEYS.get(table_name)
            valid_cols = {c.name for c in model.__table__.columns}
            
            # Introspect foreign keys for this model
            fks = {}
            for c in model.__table__.columns:
                if c.foreign_keys:
                    for fk in c.foreign_keys:
                        fks[c.name] = fk.column.table.name

            for row in records:
                if not isinstance(row, dict):
                    continue
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
                    row_unique_cols = unique_cols
                    if table_name == 'store_purchase_stock_transactions':
                        if row.get('receipt_line_id') is not None:
                            row_unique_cols = ['receipt_line_id']
                        else:
                            row_unique_cols = ['item_id', 'transaction_type', 'quantity', 'transaction_date', 'reference_no']

                    if row_unique_cols and not belongs_to_new_parent:
                        filters = {}
                        for col in row_unique_cols:
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
                row_data = {k: v for k, v in row.items() if k in valid_cols}
                if old_id and import_type == "merge":
                    row_data.pop('id', None)
                    
                new_instance = model(**row_data)
                db.add(new_instance)
                db.flush()
                
                inserted_parent_ids[table_name].add(new_instance.id)
                
                if old_id and import_type == "merge":
                    id_map[table_name][old_id] = new_instance.id
                records_inserted += 1
                    
        db.commit()

        # D. Post-Import: Recalculate all cached quantities if marketing data was restored
        if effective_scope in ("marketing", "all"):
            for po in db.query(PurchaseOrder).all():
                recalc_po_delivered_quantities(db, po)
            for wo in db.query(WorkOrder).all():
                recalc_wo_completed_quantities(db, wo)
            db.commit()

        try:
            db.execute(text("SET FOREIGN_KEY_CHECKS = 1;"))
        except Exception:
            pass
        
        return {
            "detail": "Database safely replaced." if import_type == "replace" else "Database safely merged.",
            "records_inserted": records_inserted,
            "records_skipped": records_skipped
        }
    
    except Exception as e:
        db.rollback()
        try:
            db.execute(text("SET FOREIGN_KEY_CHECKS = 1;"))
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"Merge failed, no changes made. Error: {str(e)}")
