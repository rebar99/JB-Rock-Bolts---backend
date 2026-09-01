import os

def patch_file(filepath, is_wo=False):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # 1. Imports
    if "get_user_id_from_token" not in content:
        content = content.replace(
            "from app.utils.auth import require_admin",
            "from app.utils.auth import require_admin, get_user_id_from_token\nfrom app.models.models import User"
        )
    if "from app.models.models import User" not in content:
        content = content.replace(
            "from app.models.models import",
            "from app.models.models import User,"
        )
        
    # 2. Update signature
    if is_wo:
        old_sig = "def update_work_order(wo_id: int, payload: WorkOrderUpdate, db: Session = Depends(get_db)):"
        new_sig = "def update_work_order(wo_id: int, payload: WorkOrderUpdate, authorization: str = Header(default=None), db: Session = Depends(get_db)):"
    else:
        old_sig = "def update_purchase_order(po_id: int, payload: PurchaseOrderUpdate, db: Session = Depends(get_db)):"
        new_sig = "def update_purchase_order(po_id: int, payload: PurchaseOrderUpdate, authorization: str = Header(default=None), db: Session = Depends(get_db)):"
        
    content = content.replace(old_sig, new_sig)
    
    # 3. Add quantity check logic
    old_check = "if existing_signature != new_signature:"
    
    auth_check = """
        quantity_changed = False
        var_name = getattr(po, 'po_number', getattr(po, 'wo_number', 'unknown'))
        existing_items_dict = {li.id: li for li in po.line_items if getattr(li, 'id', None)}
        for li_data in payload.line_items:
            if li_data.id and li_data.id in existing_items_dict:
                if float(li_data.quantity or 0) != float(existing_items_dict[li_data.id].quantity):
                    quantity_changed = True
                    break
            else:
                quantity_changed = True
                break
                
        if len(payload.line_items) != len(existing_items_dict):
            quantity_changed = True
            
        if quantity_changed:
            user_id = get_user_id_from_token(authorization) if authorization else None
            user = db.get(User, user_id) if user_id else None
            if not user or not user.is_admin:
                raise HTTPException(
                    status_code=403,
                    detail="Only Admin can update PO quantity." if not """ + str(is_wo) + """ else "Only Admin can update Work Order quantity."
                )

        if existing_signature != new_signature:"""
        
    if "quantity_changed = False" not in content:
        if is_wo:
            auth_check = auth_check.replace("po.", "wo.").replace("po,", "wo,")
        content = content.replace(old_check, auth_check)
        
    # 4. Add the increase quantity endpoint
    model_name = "WorkOrder" if is_wo else "PurchaseOrder"
    endpoint_prefix = "work-orders" if is_wo else "purchase-orders"
    update_model = "WorkOrderOut" if is_wo else "PurchaseOrderOut"
    var_name = "wo" if is_wo else "po"
    id_name = "wo_id" if is_wo else "po_id"
    title = "Work Order" if is_wo else "PO"
    completed_col = "completed_quantity" if is_wo else "delivered_quantity"
    
    increase_endpoint = f'''
from pydantic import BaseModel

class QuantityIncreaseItem_{var_name}(BaseModel):
    line_item_id: int
    additional_quantity: float
    reason: str

class QuantityIncreaseRequest_{var_name}(BaseModel):
    items: list[QuantityIncreaseItem_{var_name}]

@router.post("/{{{id_name}}}/increase-quantity", response_model={update_model})
def increase_{var_name}_quantity(
    {id_name}: int,
    payload: QuantityIncreaseRequest_{var_name},
    authorization: str = Header(default=None),
    db: Session = Depends(get_db)
):
    admin = require_admin(authorization, db, "Only Admin can update {title} quantity.")
    {var_name} = db.get({model_name}, {id_name})
    if not {var_name}:
        raise HTTPException(status_code=404, detail="{title} not found.")
        
    if getattr({var_name}, "short_closed", False) or getattr({var_name}, "status", "") in ["Closed", "Cancelled", "Completed"]:
        raise HTTPException(status_code=400, detail="Cannot update quantity for closed/completed {title}.")

    item_map = {{li.id: li for li in {var_name}.line_items if li.id}}
    
    logs = []
    total_added = 0
    for update_item in payload.items:
        li = item_map.get(update_item.line_item_id)
        if not li:
            raise HTTPException(status_code=400, detail=f"Line item {{update_item.line_item_id}} not found.")
            
        old_qty = li.quantity
        added_qty = update_item.additional_quantity
        new_qty = old_qty + added_qty
        
        if new_qty < getattr(li, "{completed_col}", 0):
            raise HTTPException(status_code=400, detail=f"Cannot reduce below dispatched amount for item {{li.item}}.")
            
        li.quantity = new_qty
        total_added += added_qty
        logs.append(f"Item '{{li.item}}': {{old_qty}} -> {{new_qty}} (+{{added_qty}})")
        
    db.commit()
    db.refresh({var_name})
    
    identifier = getattr({var_name}, "po_number", getattr({var_name}, "wo_number", str({id_name})))
    
    log_activity(
        db,
        action="Increase Quantity",
        entity_type="{model_name}",
        details=f"Increased {title} quantity. Reason: {{payload.items[0].reason}}. Changes: {{'; '.join(logs)}}",
        user=admin.name,
        entity_id={id_name},
        entity_name=identifier
    )
    
    return {var_name}
'''

    if f"increase_{var_name}_quantity" not in content:
        content += increase_endpoint
        
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

patch_file('app/routers/purchase_orders.py', is_wo=False)
patch_file('app/routers/work_orders.py', is_wo=True)
print("Done patching backend.")
