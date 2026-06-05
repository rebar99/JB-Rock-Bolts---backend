from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from typing import List, Optional
from app.database import get_db
from app.models.models import Client, PurchaseOrder, Record
from app.schemas.client import ClientCreate, ClientOut
from app.utils.helpers import log_activity

router = APIRouter(prefix="/api/clients", tags=["Clients"])


@router.get("", response_model=List[ClientOut])
def list_clients(location: Optional[str] = None, db: Session = Depends(get_db)):
    from app.models.models import Sale
    q = db.query(Client)
    if location:
        q = q.filter(Client.location.ilike(f"%{location}%"))
    clients = q.order_by(Client.name).all()

    # Map order counts from PurchaseOrders
    order_counts = (
        db.query(PurchaseOrder.client_name, func.count(PurchaseOrder.id).label("cnt"))
        .group_by(PurchaseOrder.client_name)
        .all()
    )
    order_map = {r.client_name: r.cnt for r in order_counts}

    # Map revenue from Sales (real invoices)
    revenue = (
        db.query(Sale.client_name, func.sum(Sale.grand_total).label("total"))
        .group_by(Sale.client_name)
        .all()
    )
    revenue_map = {r.client_name: r.total or 0 for r in revenue}

    return [
        ClientOut(
            id=c.id,
            name=c.name,
            location=c.location,
            created_at=c.created_at,
            order_count=order_map.get(c.name, 0),
            total_purchases=revenue_map.get(c.name, 0.0),
        )
        for c in clients
    ]


@router.post("", response_model=ClientOut, status_code=status.HTTP_201_CREATED)
def create_client(payload: ClientCreate, db: Session = Depends(get_db)):
    client = Client(
        name=payload.name, 
        location=payload.location,
    )
    db.add(client)
    db.commit()
    db.refresh(client)
    log_activity(db, "Client Created", "Client", f"Created client {client.name}.", "System/Admin", client.id)
    return ClientOut(
        id=client.id,
        name=client.name,
        location=client.location,
        created_at=client.created_at,
        order_count=0,
        total_purchases=0.0,
    )


@router.delete("/{client_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_client(client_id: int, db: Session = Depends(get_db)):
    client = db.get(Client, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found.")
    
    try:
        db.delete(client)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete client because it has linked Purchase Orders or Sales. Please delete them first."
        )
