from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List
from app.database import get_db
from app.models.models import Product, Sale
from app.schemas.product import ProductCreate, ProductUpdate, ProductOut
from app.utils.helpers import derive_inventory_status, log_activity

router = APIRouter(prefix="/api/inventory", tags=["Inventory"])


def _to_out(product: Product, sales_count: int = 0) -> ProductOut:
    return ProductOut(
        id=product.id,
        name=product.name,
        quantity=product.quantity,
        status=product.status,
        sales_count=sales_count,
        created_at=product.created_at,
        updated_at=product.updated_at,
    )


@router.get("", response_model=List[ProductOut])
def list_inventory(db: Session = Depends(get_db)):
    products = db.query(Product).order_by(Product.name).all()
    from app.models.models import SaleItem
    sales_counts = (
        db.query(SaleItem.item, func.count(SaleItem.id).label("cnt"))
        .group_by(SaleItem.item)
        .all()
    )
    count_map = {r.item: r.cnt for r in sales_counts}
    return [_to_out(p, count_map.get(p.name, 0)) for p in products]


@router.post("", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
def create_product(payload: ProductCreate, db: Session = Depends(get_db)):
    existing = db.query(Product).filter(Product.name == payload.name).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Product '{payload.name}' already exists.",
        )
    auto_status = payload.status or derive_inventory_status(payload.quantity)
    product = Product(name=payload.name, quantity=payload.quantity, status=auto_status)
    db.add(product)
    db.commit()
    db.refresh(product)
    return _to_out(product)


@router.put("/{product_id}", response_model=ProductOut)
def update_product(product_id: int, payload: ProductUpdate, db: Session = Depends(get_db)):
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found.")

    changed_fields = []
    if payload.quantity is not None and product.quantity != payload.quantity:
        changed_fields.append("quantity")
        product.quantity = payload.quantity
        new_status = payload.status or derive_inventory_status(payload.quantity)
        if product.status != new_status:
            changed_fields.append("status")
            product.status = new_status
    elif payload.status is not None and product.status != payload.status:
        changed_fields.append("status")
        product.status = payload.status

    db.commit()
    db.refresh(product)
    
    details_str = f"Updated product {product.name}."
    if changed_fields:
        details_str += f" Changed fields: {', '.join(changed_fields)}"
        
    log_activity(db, "Product Updated", "Product", details_str, "System/Admin", product.id)
    return _to_out(product)


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product(product_id: int, db: Session = Depends(get_db)):
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found.")
    name = product.name
    db.delete(product)
    db.commit()
    log_activity(db, "Product Deleted", "Product", f"Deleted product {name}.", "System/Admin", product_id)
