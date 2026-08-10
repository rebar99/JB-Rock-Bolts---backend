import os

router_path = r"D:\rebar-jbrocks\JB-Rock-Bolts---backend\app\routers\item_master.py"
with open(router_path, "r", encoding="utf-8") as f:
    content = f.read()

# 1. Update imports
content = content.replace(
    "from app.models.models import ItemMasterItem, ItemMasterSize",
    "from app.models.models import ItemMasterItem, ItemMasterSize, WOItemMasterItem, WOItemMasterSize"
)

# 2. Update list_items
old_list_items = """@router.get("", response_model=List[ItemMasterOut])
def list_items(db: Session = Depends(get_db)):
    \"\"\"Readable by any logged-in user (Admin or User) — this is what backs
    the PO Item field's searchable dropdown for everyone. Only the
    mutating endpoints below are Admin-gated.
    \"\"\"
    return (
        db.query(ItemMasterItem)
        .options(joinedload(ItemMasterItem.sizes))
        .order_by(ItemMasterItem.name)
        .all()
    )"""

new_list_items = """@router.get("", response_model=List[ItemMasterOut])
def list_items(type: str = "PO", db: Session = Depends(get_db)):
    if type == "WO":
        return (
            db.query(WOItemMasterItem)
            .options(joinedload(WOItemMasterItem.sizes))
            .order_by(WOItemMasterItem.name)
            .all()
        )
    return (
        db.query(ItemMasterItem)
        .options(joinedload(ItemMasterItem.sizes))
        .order_by(ItemMasterItem.name)
        .all()
    )"""
content = content.replace(old_list_items, new_list_items)

# 3. Update create_item
old_create_item = """@router.post("", response_model=ItemMasterOut, status_code=status.HTTP_201_CREATED)
def create_item(
    payload: ItemMasterCreate,
    authorization: str = Header(default=None),
    db: Session = Depends(get_db),
):
    require_admin(authorization, db, ACCESS_DENIED_DETAIL)

    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Item name is required.")

    existing = db.query(ItemMasterItem).filter(func.lower(ItemMasterItem.name) == name.lower()).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Item '{name}' already exists.")

    item = ItemMasterItem(name=name, created_by=payload.created_by or "System")
    db.add(item)
    db.commit()
    db.refresh(item)

    log_activity(db, "Item Master Item Added", "ItemMasterItem", f"Added item '{item.name}' to the Item Master.", payload.created_by or "System", item.id, entity_name=item.name)
    return item"""

new_create_item = """@router.post("", response_model=ItemMasterOut, status_code=status.HTTP_201_CREATED)
def create_item(
    payload: ItemMasterCreate,
    authorization: str = Header(default=None),
    db: Session = Depends(get_db),
):
    require_admin(authorization, db, ACCESS_DENIED_DETAIL)

    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Item name is required.")

    Model = WOItemMasterItem if payload.type == "WO" else ItemMasterItem

    existing = db.query(Model).filter(func.lower(Model.name) == name.lower()).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Item '{name}' already exists.")

    item = Model(name=name, created_by=payload.created_by or "System")
    db.add(item)
    db.commit()
    db.refresh(item)

    log_activity(db, f"Item Master Item Added ({payload.type})", Model.__name__, f"Added item '{item.name}' to the Item Master.", payload.created_by or "System", item.id, entity_name=item.name)
    return item"""
content = content.replace(old_create_item, new_create_item)


# 4. Update update_item
old_update_item = """@router.put("/{item_id}", response_model=ItemMasterOut)
def update_item(
    item_id: int,
    payload: ItemMasterUpdate,
    authorization: str = Header(default=None),
    db: Session = Depends(get_db),
):
    require_admin(authorization, db, ACCESS_DENIED_DETAIL)

    item = db.get(ItemMasterItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found.")

    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Item name is required.")

    existing = db.query(ItemMasterItem).filter(func.lower(ItemMasterItem.name) == name.lower(), ItemMasterItem.id != item_id).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Item '{name}' already exists.")

    old_name = item.name
    item.name = name
    item.updated_by = payload.updated_by or "System"
    item.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(item)

    log_activity(db, "Item Master Item Updated", "ItemMasterItem", f"Renamed item '{old_name}' to '{item.name}'.", payload.updated_by or "System", item.id, entity_name=item.name)
    return item"""

new_update_item = """@router.put("/{item_id}", response_model=ItemMasterOut)
def update_item(
    item_id: int,
    payload: ItemMasterUpdate,
    authorization: str = Header(default=None),
    db: Session = Depends(get_db),
):
    require_admin(authorization, db, ACCESS_DENIED_DETAIL)

    Model = WOItemMasterItem if payload.type == "WO" else ItemMasterItem

    item = db.get(Model, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found.")

    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Item name is required.")

    existing = db.query(Model).filter(func.lower(Model.name) == name.lower(), Model.id != item_id).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Item '{name}' already exists.")

    old_name = item.name
    item.name = name
    item.updated_by = payload.updated_by or "System"
    item.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(item)

    log_activity(db, f"Item Master Item Updated ({payload.type})", Model.__name__, f"Renamed item '{old_name}' to '{item.name}'.", payload.updated_by or "System", item.id, entity_name=item.name)
    return item"""
content = content.replace(old_update_item, new_update_item)


# 5. Update delete_item
old_delete_item = """@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(
    item_id: int,
    deleted_by: Optional[str] = None,
    authorization: str = Header(default=None),
    db: Session = Depends(get_db),
):
    require_admin(authorization, db, ACCESS_DENIED_DETAIL)

    item = db.get(ItemMasterItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found.")

    name = item.name
    db.delete(item)
    db.commit()

    log_activity(db, "Item Master Item Deleted", "ItemMasterItem", f"Deleted item '{name}' from the Item Master.", deleted_by or "System", entity_name=name)
    return None"""

new_delete_item = """@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(
    item_id: int,
    type: str = "PO",
    deleted_by: Optional[str] = None,
    authorization: str = Header(default=None),
    db: Session = Depends(get_db),
):
    require_admin(authorization, db, ACCESS_DENIED_DETAIL)

    Model = WOItemMasterItem if type == "WO" else ItemMasterItem

    item = db.get(Model, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found.")

    name = item.name
    db.delete(item)
    db.commit()

    log_activity(db, f"Item Master Item Deleted ({type})", Model.__name__, f"Deleted item '{name}' from the Item Master.", deleted_by or "System", entity_name=name)
    return None"""
content = content.replace(old_delete_item, new_delete_item)


# 6. Update add_item_size
old_add_item_size = """@router.post("/{item_id}/sizes", response_model=ItemMasterSizeOut, status_code=status.HTTP_201_CREATED)
def add_item_size(
    item_id: int,
    payload: ItemMasterSizeCreate,
    authorization: str = Header(default=None),
    db: Session = Depends(get_db),
):
    require_admin(authorization, db, ACCESS_DENIED_DETAIL)

    item = db.get(ItemMasterItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found.")

    size = (payload.size or "").strip()
    if not size:
        raise HTTPException(status_code=400, detail="Size is required.")

    existing = db.query(ItemMasterSize).filter(
        ItemMasterSize.item_id == item_id,
        func.lower(ItemMasterSize.size) == size.lower(),
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Size '{size}' already exists for '{item.name}'.")

    row = ItemMasterSize(item_id=item_id, size=size, created_by=payload.created_by or "System")
    db.add(row)
    db.commit()
    db.refresh(row)

    log_activity(db, "Item Master Size Added", "ItemMasterSize", f"Added size '{size}' to item '{item.name}'.", payload.created_by or "System", row.id, entity_name=f"{item.name} {size}")
    return row"""

new_add_item_size = """@router.post("/{item_id}/sizes", response_model=ItemMasterSizeOut, status_code=status.HTTP_201_CREATED)
def add_item_size(
    item_id: int,
    payload: ItemMasterSizeCreate,
    authorization: str = Header(default=None),
    db: Session = Depends(get_db),
):
    require_admin(authorization, db, ACCESS_DENIED_DETAIL)

    Model = WOItemMasterItem if payload.type == "WO" else ItemMasterItem
    SizeModel = WOItemMasterSize if payload.type == "WO" else ItemMasterSize

    item = db.get(Model, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found.")

    size = (payload.size or "").strip()
    if not size:
        raise HTTPException(status_code=400, detail="Size is required.")

    existing = db.query(SizeModel).filter(
        SizeModel.item_id == item_id,
        func.lower(SizeModel.size) == size.lower(),
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Size '{size}' already exists for '{item.name}'.")

    row = SizeModel(item_id=item_id, size=size, created_by=payload.created_by or "System")
    db.add(row)
    db.commit()
    db.refresh(row)

    log_activity(db, f"Item Master Size Added ({payload.type})", SizeModel.__name__, f"Added size '{size}' to item '{item.name}'.", payload.created_by or "System", row.id, entity_name=f"{item.name} {size}")
    return row"""
content = content.replace(old_add_item_size, new_add_item_size)


# 7. Update delete_item_size
old_delete_item_size = """@router.delete("/{item_id}/sizes/{size_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item_size(
    item_id: int,
    size_id: int,
    deleted_by: Optional[str] = None,
    authorization: str = Header(default=None),
    db: Session = Depends(get_db),
):
    require_admin(authorization, db, ACCESS_DENIED_DETAIL)

    row = db.query(ItemMasterSize).filter(ItemMasterSize.id == size_id, ItemMasterSize.item_id == item_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Size not found.")

    label = f"{row.item.name} {row.size}"
    db.delete(row)
    db.commit()

    log_activity(db, "Item Master Size Deleted", "ItemMasterSize", f"Deleted size '{label}'.", deleted_by or "System", entity_name=label)
    return None"""

new_delete_item_size = """@router.delete("/{item_id}/sizes/{size_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item_size(
    item_id: int,
    size_id: int,
    type: str = "PO",
    deleted_by: Optional[str] = None,
    authorization: str = Header(default=None),
    db: Session = Depends(get_db),
):
    require_admin(authorization, db, ACCESS_DENIED_DETAIL)

    SizeModel = WOItemMasterSize if type == "WO" else ItemMasterSize

    row = db.query(SizeModel).filter(SizeModel.id == size_id, SizeModel.item_id == item_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Size not found.")

    label = f"{row.item.name} {row.size}"
    db.delete(row)
    db.commit()

    log_activity(db, f"Item Master Size Deleted ({type})", SizeModel.__name__, f"Deleted size '{label}'.", deleted_by or "System", entity_name=label)
    return None"""
content = content.replace(old_delete_item_size, new_delete_item_size)

with open(router_path, "w", encoding="utf-8") as f:
    f.write(content)
print("Updated backend router!")
