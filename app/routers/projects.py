from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional
from app.database import get_db
from app.models.models import Project, Client, WorkOrder
from app.schemas.project import ProjectCreate, ProjectOut
from app.utils.helpers import log_activity, normalize_client_name, normalize_project_name

router = APIRouter(prefix="/api/projects", tags=["Projects"])

@router.get("", response_model=List[ProjectOut])
def list_projects(client_id: int = None, client_name: str = None, source: Optional[str] = None, db: Session = Depends(get_db)):
    q = db.query(Project)
    if client_id:
        q = q.filter(Project.client_id == client_id)
    if client_name:
        # Matched by normalized name, not exact string, so selecting any
        # spelling variant of a client ("M/s. Afcons" vs "M/s. Afcons
        # Infrastructure Limited") still surfaces every project tied to
        # that client, regardless of which duplicate Client row it's on.
        target_key = normalize_client_name(client_name)
        matching_client_ids = [
            c.id for c in db.query(Client.id, Client.name).all()
            if normalize_client_name(c.name) == target_key
        ]
        if not matching_client_ids:
            return []
        q = q.filter(Project.client_id.in_(matching_client_ids))

    projects = q.order_by(Project.name).all()

    # Collapse near-duplicate project names (case/spacing variants) to one
    # representative row per normalized name — the same rationale as the
    # client-name dedup above, since duplicate Client rows can each carry
    # their own copy of "the same" project under a slightly different
    # spelling.
    seen = set()
    deduped = []
    for p in projects:
        key = normalize_project_name(p.name)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(p)

    # Work Orders draw their "Name of Project" dropdown from projects that
    # actually have a Work Order on record — not every project tied to the
    # client, which also includes ones only ever used on a Purchase Order.
    # Matched via WorkOrder.project (free-typed text field) rather than the
    # project_id FK, since older Work Orders may not have that FK set.
    if source == "wo":
        wo_project_keys = {
            normalize_project_name(p) for (p,) in db.query(WorkOrder.project).filter(WorkOrder.project.isnot(None)).all()
            if normalize_project_name(p)
        }
        deduped = [p for p in deduped if normalize_project_name(p.name) in wo_project_keys]

    return deduped

@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)):
    client = db.query(Client).filter(Client.name == payload.client_name).first()
    if not client:
        client = Client(name=payload.client_name, location="Unknown")
        db.add(client)
        db.commit()
        db.refresh(client)
        
    project = Project(name=payload.name, client_id=client.id)
    db.add(project)
    db.commit()
    db.refresh(project)
    log_activity(db, "Project Created", "Project", f"Created project {project.name} for client {client.name}.", payload.created_by or "System", project.id, entity_name=project.name)
    return project

@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: int, deleted_by: Optional[str] = None, db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found.")
    name = project.name
    db.delete(project)
    db.commit()
    log_activity(db, "Project Deleted", "Project", f"Deleted project {name}.", deleted_by or "System", project_id, entity_name=name)
