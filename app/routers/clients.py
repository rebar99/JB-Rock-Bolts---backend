from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from typing import List, Optional
from app.database import get_db
from app.models.models import Client, PurchaseOrder, Record
from app.schemas.client import ClientCreate, ClientOut, ClientStats, MergeClientsRequest
from app.utils.helpers import log_activity, normalize_client_name

router = APIRouter(prefix="/api/clients", tags=["Clients"])


@router.get("/stats", response_model=ClientStats)
def get_client_stats(db: Session = Depends(get_db)):
    """Total Clients — recalculated from scratch on every request. Client
    names are normalized (case, spacing, punctuation, legal-entity suffixes
    like Ltd/Pvt/Limited) before counting, using the exact same normalization
    as the Dashboard, so 'M/s. Afcons', 'AFCONS', and 'afcons' are counted as
    a single client here and everywhere else in the app.
    """
    all_names = db.query(Client.name).all()
    normalized_names = set()
    for row in all_names:
        n = normalize_client_name(row.name)
        if n:
            normalized_names.add(n)
    return ClientStats(total_clients=len(normalized_names))


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
    new_norm = normalize_client_name(payload.name)
    all_clients = db.query(Client).all()
    for existing in all_clients:
        if normalize_client_name(existing.name) == new_norm:
            raise HTTPException(
                status_code=400,
                detail=f"A client with a similar name ({existing.name}) already exists. Please use it to avoid duplicates."
            )

    client = Client(
        name=payload.name, 
        location=payload.location,
    )
    db.add(client)
    db.commit()
    db.refresh(client)
    log_activity(db, "Client Created", "Client", f"Created client {client.name}.", payload.created_by or "System", client.id, entity_name=client.name)
    return ClientOut(
        id=client.id,
        name=client.name,
        location=client.location,
        created_at=client.created_at,
        order_count=0,
        total_purchases=0.0,
    )


@router.post("/merge", status_code=status.HTTP_200_OK)
def merge_clients(payload: MergeClientsRequest, db: Session = Depends(get_db)):
    from app.models.models import PurchaseOrder, Sale, Record, WorkOrder, WorkOrderSale, Project
    
    master = db.get(Client, payload.master_id)
    if not master:
        raise HTTPException(status_code=404, detail="Master client not found.")

    duplicates = db.query(Client).filter(Client.id.in_(payload.duplicate_ids)).all()
    if not duplicates:
        raise HTTPException(status_code=404, detail="No duplicate clients found.")

    for duplicate in duplicates:
        if duplicate.id == master.id:
            continue
            
        # 1. Update Projects
        db.query(Project).filter(Project.client_id == duplicate.id).update(
            {"client_id": master.id}, synchronize_session=False
        )

        # 2. Update PurchaseOrders
        db.query(PurchaseOrder).filter(
            (PurchaseOrder.client_id == duplicate.id) | (func.lower(func.trim(PurchaseOrder.client_name)) == func.lower(duplicate.name.strip()))
        ).update(
            {"client_id": master.id, "client_name": master.name}, synchronize_session=False
        )

        # 3. Update Sales
        db.query(Sale).filter(func.lower(func.trim(Sale.client_name)) == func.lower(duplicate.name.strip())).update(
            {"client_name": master.name}, synchronize_session=False
        )

        # 4. Update Records
        db.query(Record).filter(
            (Record.client_id == duplicate.id) | (func.lower(func.trim(Record.client_name)) == func.lower(duplicate.name.strip()))
        ).update(
            {"client_id": master.id, "client_name": master.name}, synchronize_session=False
        )

        # 5. Update WorkOrders
        db.query(WorkOrder).filter(
            (WorkOrder.client_id == duplicate.id) | (func.lower(func.trim(WorkOrder.client_name)) == func.lower(duplicate.name.strip()))
        ).update(
            {"client_id": master.id, "client_name": master.name}, synchronize_session=False
        )

        # 6. Update WorkOrderSales
        db.query(WorkOrderSale).filter(func.lower(func.trim(WorkOrderSale.client_name)) == func.lower(duplicate.name.strip())).update(
            {"client_name": master.name}, synchronize_session=False
        )

        # Delete duplicate
        db.delete(duplicate)

        log_activity(
            db, 
            "Client Merged", 
            "Client", 
            f"Merged duplicate client '{duplicate.name}' into '{master.name}'.", 
            payload.merged_by or "System",
            master.id,
            entity_name=master.name
        )
    
    db.commit()
    return {"message": f"Successfully merged into {master.name}"}


@router.delete("/{client_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_client(client_id: int, deleted_by: Optional[str] = None, db: Session = Depends(get_db)):
    client = db.get(Client, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found.")

    client_name = client.name
    try:
        db.delete(client)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete client because it has linked Purchase Orders or Sales. Please delete them first."
        )
    log_activity(db, "Client Deleted", "Client", f"Deleted client {client_name}.", deleted_by or "System", client_id, entity_name=client_name)
