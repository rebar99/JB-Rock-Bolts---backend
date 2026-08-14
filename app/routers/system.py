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
        if isinstance(obj, datetime.datetime):
            return obj.isoformat()
        if isinstance(obj, datetime.date):
            return obj.isoformat()
        if isinstance(obj, decimal.Decimal):
            return float(obj)
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
            r_dict = r.__dict__.copy()
            r_dict.pop('_sa_instance_state', None)
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

@router.post("/backup/import")
async def import_database(
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    """
    Import a complete database JSON dump.
    WARNING: This wipes existing data and replaces it with the backup.
    """
    if not file.filename.endswith(".json"):
        raise HTTPException(status_code=400, detail="Only JSON files are allowed.")
    
    try:
        content = await file.read()
        backup_data = json.loads(content)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON file: {str(e)}")

    try:
        # Disable foreign key checks
        db.execute(text("SET FOREIGN_KEY_CHECKS=0;"))
        
        # 1. Delete all existing data
        # Reverse order to delete children before parents
        for model in reversed(MODELS):
            table_name = model.__tablename__
            db.execute(text(f"DELETE FROM {table_name}"))
            
        # 2. Insert records from backup
        for model in MODELS:
            table_name = model.__tablename__
            records = backup_data.get(table_name, [])
            if not records:
                continue
                
            # Date/Time parsing for records is mostly handled by SQLAlchemy if formatted as ISO,
            # but we need to ensure strings are parsed into Python objects if SQLAlchemy expects them.
            # Convert ISO datetime strings back to datetime objects where necessary because bulk_insert_mappings
            # bypassing ORM might have strict type requirements for some drivers.
            for record in records:
                for key, value in record.items():
                    if isinstance(value, str):
                        try:
                            # Basic check for isoformat (len 10 for date, >=19 for datetime)
                            if len(value) == 10 and value.count("-") == 2:
                                record[key] = datetime.datetime.strptime(value, "%Y-%m-%d").date()
                            elif "T" in value and (len(value) == 19 or "." in value):
                                record[key] = datetime.datetime.fromisoformat(value)
                        except (ValueError, TypeError):
                            pass

            db.bulk_insert_mappings(model, records)
            
        # Re-enable foreign key checks
        db.execute(text("SET FOREIGN_KEY_CHECKS=1;"))
        db.commit()
        
        return {"detail": "Data imported successfully. All Purchase Orders, Work Orders, Sales and related records have been restored."}
    
    except Exception as e:
        db.rollback()
        db.execute(text("SET FOREIGN_KEY_CHECKS=1;"))
        raise HTTPException(status_code=500, detail=f"Database import failed: {str(e)}")
