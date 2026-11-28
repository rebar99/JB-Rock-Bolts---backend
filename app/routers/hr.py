"""
HR Department – FastAPI Router
Endpoints: /api/hr/...
"""
import calendar
import os
import re
from datetime import date, datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import (
    HRAttendance,
    HRAttendanceClose,
    HREmployee,
    HRSalary,
    HRSalaryAudit,
    HRSalaryConfig,
    SystemLog,
    User,
)
from app.routers.upload_helpers import read_upload_bytes, save_upload_bytes
from app.utils.auth import get_current_user
from app import notifications

router = APIRouter(prefix="/api/hr", tags=["HR Department"])

# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

VALID_STATUSES = {"P", "A", "H", "HD", "HOL", "HP", "L", "PL", "UL", "WO", "TR"}

DEFAULT_SALARY_CONFIG = [
    ("working_days_per_month", "26", "Working days considered for full salary calculation"),
    ("half_day_factor", "0.5", "Half-day counts as this fraction of a full day"),
    ("overtime_rate_per_hour", "0", "Overtime rate per hour (Rs.)"),
    ("ot_hours_divisor", "12", "OT hours divisor per day (daily working hours base, default 12)"),
    ("pf_percentage", "12", "PF % of Basic Salary"),
    ("esi_percentage", "0.75", "ESI % of Gross Salary (applied only if gross <= 21000)"),
    ("hra_percentage", "40", "HRA % of Basic Salary"),
    ("conveyance_allowance", "1600", "Monthly Conveyance Allowance (Rs.)"),
    ("medical_allowance", "1250", "Monthly Medical Allowance (Rs.)"),
    ("travel_allowance_rate", "0", "Travel Allowance per travel day (Rs.)"),
    ("tds_percentage", "0", "TDS % of Gross Salary"),
]


def _ensure_salary_config(db: Session):
    for key, val, desc in DEFAULT_SALARY_CONFIG:
        exists = db.query(HRSalaryConfig).filter(HRSalaryConfig.config_key == key).first()
        if not exists:
            db.add(HRSalaryConfig(config_key=key, config_value=val, description=desc))
    db.commit()


def _get_config(db: Session) -> dict:
    _ensure_salary_config(db)
    rows = db.query(HRSalaryConfig).all()
    return {r.config_key: r.config_value for r in rows}


def _next_employee_code(db: Session) -> str:
    last = (
        db.query(HREmployee.employee_code)
        .order_by(HREmployee.id.desc())
        .first()
    )
    if not last:
        return "EMP-001"
    try:
        num = int(last[0].split("-")[-1])
        return f"EMP-{num + 1:03d}"
    except Exception:
        count = db.query(func.count(HREmployee.id)).scalar() or 0
        return f"EMP-{count + 1:03d}"


def _mask_aadhaar(val):
    if not val or len(val) < 4:
        return val
    return "XXXX-XXXX-" + val[-4:]


def _mask_pan(val):
    if not val or len(val) < 10:
        return val
    return val[:2] + "***" + val[5:]


def _employee_to_dict(emp: HREmployee, detail: bool = False) -> dict:
    today = date.today()
    age = None
    if emp.date_of_birth:
        age = today.year - emp.date_of_birth.year - (
            (today.month, today.day) < (emp.date_of_birth.month, emp.date_of_birth.day)
        )
    experience = None
    if emp.date_of_joining:
        delta_months = (today.year - emp.date_of_joining.year) * 12 + (
            today.month - emp.date_of_joining.month
        )
        years, months = divmod(max(0, delta_months), 12)
        if years and months:
            experience = f"{years} yr {months} mo"
        elif years:
            experience = f"{years} yr"
        else:
            experience = f"{months} mo"

    acc_num = emp.bank_account_number
    masked_acc = ("XXXX" + acc_num[-4:]) if acc_num and len(acc_num) >= 4 else acc_num

    return {
        "id": emp.id,
        "display_order": getattr(emp, "display_order", 0) or 0,
        "employee_code": emp.employee_code,
        "name": emp.name,
        "designation": emp.designation,
        "father_name": emp.father_name,
        "gender": emp.gender,
        "marital_status": emp.marital_status,
        "email": emp.email,
        "date_of_birth": emp.date_of_birth.isoformat() if emp.date_of_birth else None,
        "age": age,
        "mobile": emp.mobile,
        "address": emp.address,
        "date_of_joining": emp.date_of_joining.isoformat() if emp.date_of_joining else None,
        "experience": experience,
        "starting_salary": float(emp.starting_salary or 0),
        "present_salary": float(emp.present_salary or 0),
        "location": emp.location,
        "qualification": emp.qualification,
        "qualification_doc_url": emp.qualification_doc_url if detail else None,
        "aadhaar_number": emp.aadhaar_number if detail else _mask_aadhaar(emp.aadhaar_number),
        "pan_number": emp.pan_number if detail else _mask_pan(emp.pan_number),
        "bank_account_holder": emp.bank_account_holder,
        "bank_name": emp.bank_name,
        "bank_account_number": emp.bank_account_number if detail else masked_acc,
        "bank_ifsc": emp.bank_ifsc,
        "bank_branch": emp.bank_branch,
        "profile_image_url": emp.profile_image_url,
        "aadhaar_doc_url": emp.aadhaar_doc_url if detail else None,
        "pan_doc_url": emp.pan_doc_url if detail else None,
        "bank_doc_url": emp.bank_doc_url if detail else None,
        "is_active": emp.is_active,
        "inactive_date": emp.inactive_date.isoformat() if getattr(emp, "inactive_date", None) else None,
        "created_by": emp.created_by,
        "updated_by": emp.updated_by,
        "created_at": emp.created_at.isoformat() if emp.created_at else None,
        "updated_at": emp.updated_at.isoformat() if emp.updated_at else None,
        "last_opened_by": getattr(emp, "last_opened_by", None),
        "last_opened_at": getattr(emp, "last_opened_at", None).isoformat() if getattr(emp, "last_opened_at", None) else None,
    }


# ─────────────────────────────────────────────
# Pydantic Schemas
# ─────────────────────────────────────────────

class EmployeeCreate(BaseModel):
    employee_code: Optional[str] = None
    name: str
    designation: Optional[str] = None
    father_name: Optional[str] = None
    gender: Optional[str] = None
    marital_status: Optional[str] = None
    email: Optional[str] = None
    date_of_birth: Optional[date] = None
    mobile: Optional[str] = None
    address: Optional[str] = None
    date_of_joining: Optional[date] = None
    starting_salary: Optional[float] = 0
    present_salary: Optional[float] = 0
    location: Optional[str] = None
    qualification: Optional[str] = None
    qualification_doc_url: Optional[str] = None
    aadhaar_number: Optional[str] = None
    pan_number: Optional[str] = None
    bank_account_holder: Optional[str] = None
    bank_name: Optional[str] = None
    bank_account_number: Optional[str] = None
    bank_ifsc: Optional[str] = None
    bank_branch: Optional[str] = None


class EmployeeUpdate(EmployeeCreate):
    name: Optional[str] = None


class EmployeeReorderPayload(BaseModel):
    employee_ids: List[int]


class AttendanceMark(BaseModel):
    employee_id: int
    attendance_date: date
    status: str


class BulkAttendanceMark(BaseModel):
    records: List[AttendanceMark]


class AttendanceCloseRequest(BaseModel):
    month: int
    year: int
    notes: Optional[str] = None


class SalaryGenerateRequest(BaseModel):
    employee_id: int
    month: int
    year: int
    overtime_amount: Optional[float] = 0
    allowances: Optional[float] = 0
    bonus: Optional[float] = 0
    advance_deduction: Optional[float] = 0
    pf_deduction: Optional[float] = None
    esi_deduction: Optional[float] = None
    tds_deduction: Optional[float] = None
    other_deductions: Optional[float] = 0
    travel_allowance_amount: Optional[float] = 0
    hra_amount: Optional[float] = None
    conveyance_amount: Optional[float] = None
    medical_amount: Optional[float] = None
    remarks: Optional[str] = None


class SalaryUpdateRequest(BaseModel):
    overtime_amount: Optional[float] = None
    travel_allowance_amount: Optional[float] = None
    allowances: Optional[float] = None
    bonus: Optional[float] = None
    advance_deduction: Optional[float] = None
    pf_deduction: Optional[float] = None
    esi_deduction: Optional[float] = None
    tds_deduction: Optional[float] = None
    other_deductions: Optional[float] = None
    hra_amount: Optional[float] = None
    conveyance_amount: Optional[float] = None
    medical_amount: Optional[float] = None
    remarks: Optional[str] = None


class MarkPaidRequest(BaseModel):
    payment_date: date
    payment_mode: str
    transaction_ref: Optional[str] = None
    remarks: Optional[str] = None


class SalaryConfigUpdate(BaseModel):
    configs: dict


class ToggleStatusRequest(BaseModel):
    inactive_date: Optional[date] = None


# ─────────────────────────────────────────────
# Dashboard
# ─────────────────────────────────────────────

@router.get("/dashboard")
def hr_dashboard(
    month: Optional[int] = Query(None),
    year: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    today = date.today()
    sel_month = month or today.month
    sel_year = year or today.year

    MONTH_NAMES = [
        "", "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December"
    ]

    total_emp = db.query(func.count(HREmployee.id)).scalar() or 0
    active_emp = db.query(func.count(HREmployee.id)).filter(HREmployee.is_active == True).scalar() or 0

    # Today's live attendance
    present_today = db.query(func.count(HRAttendance.id)).filter(
        HRAttendance.attendance_date == today, HRAttendance.status.in_(["P", "TR"]),
    ).scalar() or 0

    leave_today = db.query(func.count(HRAttendance.id)).filter(
        HRAttendance.attendance_date == today, HRAttendance.status.in_(["L", "PL", "UL", "A"]),
    ).scalar() or 0

    # Selected month attendance close status
    att_close = db.query(HRAttendanceClose).filter(
        HRAttendanceClose.month == sel_month, HRAttendanceClose.year == sel_year,
    ).first()
    is_attendance_closed = bool(att_close and att_close.is_closed)

    employees_present_in_month = db.query(func.count(func.distinct(HRAttendance.employee_id))).filter(
        HRAttendance.month == sel_month, HRAttendance.year == sel_year, HRAttendance.status.in_(["P", "TR"]),
    ).scalar() or 0

    employees_on_leave_in_month = db.query(func.count(func.distinct(HRAttendance.employee_id))).filter(
        HRAttendance.month == sel_month, HRAttendance.year == sel_year, HRAttendance.status.in_(["L", "PL", "UL", "A"]),
    ).scalar() or 0

    # Selected month salaries
    month_salaries = db.query(HRSalary).filter(
        HRSalary.month == sel_month, HRSalary.year == sel_year,
    ).all()

    monthly_total = sum(float(s.net_salary or 0) for s in month_salaries)
    salary_paid = sum(float(s.net_salary or 0) for s in month_salaries if s.payment_status == "Paid")
    salary_pending = sum(float(s.net_salary or 0) for s in month_salaries if s.payment_status in ("Pending", "Approved"))
    salary_on_hold = sum(float(s.net_salary or 0) for s in month_salaries if s.payment_status == "On Hold")

    salaries_list = []
    for s in month_salaries:
        salaries_list.append({
            "id": s.id,
            "employee_id": s.employee_id,
            "employee_name": s.employee.name if s.employee else "Unknown",
            "employee_code": s.employee.employee_code if s.employee else "",
            "designation": s.employee.designation if s.employee else "",
            "location": s.employee.location if s.employee else "",
            "present_days": float(s.present_days or 0),
            "basic_salary": float(s.basic_salary or 0),
            "total_deductions": float(s.total_deductions or 0),
            "net_salary": float(s.net_salary or 0),
            "payment_status": s.payment_status,
            "payment_date": s.payment_date.isoformat() if s.payment_date else None,
            "payment_mode": s.payment_mode or "",
        })

    # Historical monthly summary (all months that have salary or attendance records)
    months_seen = set()
    sal_months = db.query(HRSalary.year, HRSalary.month).distinct().all()
    for y, m in sal_months:
        months_seen.add((y, m))
    att_months = db.query(HRAttendanceClose.year, HRAttendanceClose.month).distinct().all()
    for y, m in att_months:
        months_seen.add((y, m))
    months_seen.add((today.year, today.month))

    sorted_months = sorted(list(months_seen), key=lambda x: (x[0], x[1]), reverse=True)
    history_records = []
    for y, m in sorted_months:
        m_sals = db.query(HRSalary).filter(HRSalary.month == m, HRSalary.year == y).all()
        m_close = db.query(HRAttendanceClose).filter(HRAttendanceClose.month == m, HRAttendanceClose.year == y).first()
        m_total = sum(float(s.net_salary or 0) for s in m_sals)
        m_paid = sum(float(s.net_salary or 0) for s in m_sals if s.payment_status == "Paid")
        m_pending = sum(float(s.net_salary or 0) for s in m_sals if s.payment_status in ("Pending", "Approved"))
        history_records.append({
            "year": y,
            "month": m,
            "month_name": MONTH_NAMES[m],
            "total_employees": len(m_sals),
            "total_payout": m_total,
            "salary_paid": m_paid,
            "salary_pending": m_pending,
            "is_attendance_closed": bool(m_close and m_close.is_closed),
            "closed_by": m_close.closed_by if m_close and m_close.is_closed else None,
            "salaries_count": len(m_sals),
        })

    return {
        "selected_period": {
            "month": sel_month,
            "year": sel_year,
            "month_name": MONTH_NAMES[sel_month],
            "is_current_month": (sel_month == today.month and sel_year == today.year),
        },
        "total_employees": total_emp,
        "active_employees": active_emp,
        "present_today": present_today,
        "on_leave_today": leave_today,
        "employees_present_in_month": employees_present_in_month,
        "employees_on_leave_in_month": employees_on_leave_in_month,
        "is_attendance_closed": is_attendance_closed,
        "attendance_closed_by": att_close.closed_by if att_close and att_close.is_closed else None,
        "attendance_closed_at": att_close.closed_at.isoformat() if att_close and att_close.closed_at else None,
        "monthly_salary_total": monthly_total,
        "salary_paid": salary_paid,
        "salary_pending": salary_pending,
        "salary_on_hold": salary_on_hold,
        "salaries_count": len(month_salaries),
        "salaries": salaries_list,
        "monthly_history": history_records,
    }


# ─────────────────────────────────────────────
# Employees
# ─────────────────────────────────────────────

@router.get("/employees/meta/designations")
def get_designations(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    rows = db.query(HREmployee.designation).filter(HREmployee.designation != None).distinct().all()
    return sorted([r[0] for r in rows if r[0]])


@router.get("/employees/meta/locations")
def get_locations(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    rows = db.query(HREmployee.location).filter(HREmployee.location != None).distinct().all()
    return sorted([r[0] for r in rows if r[0]])


@router.get("/employees/meta/next-code")
def get_next_employee_code(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return {"next_code": _next_employee_code(db)}


@router.get("/employees")
def list_employees(
    is_active: Optional[bool] = None,
    designation: Optional[str] = Query(None),
    location: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = db.query(HREmployee)
    if is_active is not None:
        q = q.filter(HREmployee.is_active == is_active)
    if designation:
        q = q.filter(HREmployee.designation.ilike(f"%{designation}%"))
    if location:
        q = q.filter(HREmployee.location.ilike(f"%{location}%"))
    if search:
        q = q.filter(
            HREmployee.name.ilike(f"%{search}%")
            | HREmployee.employee_code.ilike(f"%{search}%")
            | HREmployee.mobile.ilike(f"%{search}%")
        )
    employees = q.order_by(HREmployee.display_order.asc(), HREmployee.id.asc()).all()
    return [_employee_to_dict(e, detail=False) for e in employees]


@router.put("/employees/reorder")
@router.post("/employees/reorder")
def reorder_employees(
    payload: EmployeeReorderPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not payload.employee_ids:
        raise HTTPException(400, "employee_ids list cannot be empty")
    for order_idx, emp_id in enumerate(payload.employee_ids, start=1):
        db.query(HREmployee).filter(HREmployee.id == emp_id).update({"display_order": order_idx})
    db.commit()

    try:
        log = SystemLog(
            action="Employee Arrangement Updated",
            entity_type="HREmployee",
            details=f"Admin {current_user.name} saved new employee display arrangement for {len(payload.employee_ids)} employees.",
            changed_fields=f"Reordered {len(payload.employee_ids)} employees",
            user=current_user.name,
            workspace="HR",
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        notifications.broadcast(log)
    except Exception:
        pass

    return {"success": True, "reordered_count": len(payload.employee_ids)}


@router.post("/employees", status_code=201)
def create_employee(
    payload: EmployeeCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    req_code = (payload.employee_code or "").strip().upper()
    code = req_code if req_code else _next_employee_code(db)
    existing = db.query(HREmployee).filter(HREmployee.employee_code == code).first()
    if existing:
        raise HTTPException(400, f"Employee code '{code}' already exists. Please choose a unique code.")
    data = payload.model_dump(exclude_unset=True)
    if data.get("mobile"):
        mob = re.sub(r"\D", "", data["mobile"])
        if len(mob) != 10:
            raise HTTPException(400, "Mobile number exactly 10 digits hona chahiye.")
        data["mobile"] = mob
    data["employee_code"] = code
    max_order = db.query(func.max(HREmployee.display_order)).scalar() or 0
    emp = HREmployee(created_by=current_user.name, display_order=max_order + 1, **data)
    db.add(emp)
    db.commit()
    db.refresh(emp)
    # ── Activity log ──────────────────────────────────────────────────────
    try:
        log = SystemLog(
            action="Employee Added",
            entity_type="HREmployee",
            entity_id=emp.id,
            entity_name=emp.name,
            details=f"New employee {emp.name} ({emp.employee_code}) added. Designation: {emp.designation or '—'}, Location: {emp.location or '—'}, Salary: ₹{float(emp.present_salary or 0):,.2f}.",
            changed_fields=f"Code: {emp.employee_code}, Desig: {emp.designation or '—'}, Salary: ₹{float(emp.present_salary or 0):,.2f}",
            user=current_user.name,
            workspace="HR",
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        notifications.broadcast(log)
    except Exception:
        pass
    return _employee_to_dict(emp, detail=True)


@router.get("/employees/{emp_id}")
def get_employee(emp_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    emp = db.get(HREmployee, emp_id)
    if not emp:
        raise HTTPException(404, "Employee not found")
    data = _employee_to_dict(emp, detail=True)
    logs = (
        db.query(SystemLog)
        .filter(
            SystemLog.entity_type == "HREmployee",
            (SystemLog.entity_id == emp.id)
            | (SystemLog.entity_name == emp.name)
            | (SystemLog.details.like(f"%({emp.employee_code})%")),
        )
        .order_by(SystemLog.created_at.desc())
        .limit(10)
        .all()
    )
    data["history"] = [
        {
            "id": l.id,
            "action": l.action,
            "details": l.details,
            "changed_fields": l.changed_fields,
            "user": l.user,
            "created_at": l.created_at.isoformat() if l.created_at else None,
        }
        for l in logs
    ]
    return data


@router.put("/employees/{emp_id}")
def update_employee(
    emp_id: int,
    payload: EmployeeUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    emp = db.get(HREmployee, emp_id)
    if not emp:
        raise HTTPException(404, "Employee not found")
    data = payload.model_dump(exclude_unset=True)
    if "employee_code" in data and data["employee_code"]:
        req_code = data["employee_code"].strip().upper()
        if req_code != emp.employee_code:
            existing = db.query(HREmployee).filter(HREmployee.employee_code == req_code, HREmployee.id != emp.id).first()
            if existing:
                raise HTTPException(400, f"Employee code '{req_code}' already exists. Please choose a unique code.")
            data["employee_code"] = req_code
    if data.get("mobile"):
        mob = re.sub(r"\D", "", data["mobile"])
        if len(mob) != 10:
            raise HTTPException(400, "Mobile number exactly 10 digits hona chahiye.")
        data["mobile"] = mob

    FIELD_LABELS = {
        "employee_code": "Emp Code",
        "name": "Name",
        "father_name": "Father Name",
        "designation": "Designation",
        "location": "Location",
        "mobile": "Mobile",
        "email": "Email",
        "date_of_birth": "DOB",
        "date_of_joining": "Joining Date",
        "inactive_date": "Inactive Date",
        "blood_group": "Blood Group",
        "emergency_contact": "Emergency Contact",
        "permanent_address": "Permanent Address",
        "current_address": "Current Address",
        "aadhar_no": "Aadhar No",
        "pan_no": "PAN No",
        "bank_name": "Bank Name",
        "bank_account_no": "Account No",
        "ifsc_code": "IFSC Code",
        "starting_salary": "Starting Salary",
        "present_salary": "Present Salary",
        "experience": "Experience",
        "remarks": "Remarks",
        "is_active": "Status",
    }
    changes = []
    for k, v in data.items():
        old_val = getattr(emp, k, None)
        old_str = "" if old_val is None else str(old_val).strip()
        new_str = "" if v is None else str(v).strip()
        if old_str != new_str:
            label = FIELD_LABELS.get(k, k.replace("_", " ").title())
            if k in ("present_salary", "starting_salary"):
                try:
                    old_display = f"₹{float(old_val or 0):,.2f}"
                    new_display = f"₹{float(v or 0):,.2f}"
                except Exception:
                    old_display = old_str or "—"
                    new_display = new_str or "—"
                changes.append(f"{label}: {old_display} → {new_display}")
            else:
                changes.append(f"{label}: '{old_str or '—'}' → '{new_str or '—'}'")
        setattr(emp, k, v)

    emp.updated_by = current_user.name
    db.commit()
    db.refresh(emp)
    # ── Activity log ──────────────────────────────────────────────────────
    try:
        details_text = f"Employee {emp.name} ({emp.employee_code}) updated: {'; '.join(changes)}" if changes else f"Employee {emp.name} ({emp.employee_code}) details updated."
        changed_fields_text = ", ".join(changes) if changes else None
        log = SystemLog(
            action="Employee Updated",
            entity_type="HREmployee",
            entity_id=emp.id,
            entity_name=emp.name,
            details=details_text,
            changed_fields=changed_fields_text,
            user=current_user.name,
            workspace="HR",
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        notifications.broadcast(log)
    except Exception:
        pass
    return _employee_to_dict(emp, detail=True)


@router.patch("/employees/{emp_id}/toggle-status")
def toggle_employee_status(
    emp_id: int,
    payload: Optional[ToggleStatusRequest] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    emp = db.get(HREmployee, emp_id)
    if not emp:
        raise HTTPException(404, "Employee not found")
    emp.is_active = not emp.is_active
    if not emp.is_active:
        emp.inactive_date = (payload.inactive_date if (payload and payload.inactive_date) else date.today())
    else:
        emp.inactive_date = None
    emp.updated_by = current_user.name
    db.commit()
    db.refresh(emp)
    # ── Activity log ──────────────────────────────────────────────────────
    try:
        status_label = "Activated" if emp.is_active else "Deactivated"
        log = SystemLog(
            action=f"Employee {status_label}",
            entity_type="HREmployee",
            entity_id=emp.id,
            entity_name=emp.name,
            details=f"Employee {emp.name} ({emp.employee_code}) status changed to {status_label} by {current_user.name}." + (f" Inactive Date: {emp.inactive_date}" if not emp.is_active and emp.inactive_date else ""),
            changed_fields=f"Status: {status_label}",
            user=current_user.name,
            workspace="HR",
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        notifications.broadcast(log)
    except Exception:
        pass
    return _employee_to_dict(emp, detail=True)


@router.delete("/employees/{emp_id}")
def delete_employee(
    emp_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    emp = db.get(HREmployee, emp_id)
    if not emp:
        raise HTTPException(404, "Employee not found")

    # Delete related salary audits and salaries
    salaries = db.query(HRSalary).filter(HRSalary.employee_id == emp_id).all()
    for sal in salaries:
        db.query(HRSalaryAudit).filter(HRSalaryAudit.salary_id == sal.id).delete()
        db.delete(sal)

    # Delete related attendance records
    db.query(HRAttendance).filter(HRAttendance.employee_id == emp_id).delete()

    emp_name = emp.name
    emp_code = emp.employee_code
    db.delete(emp)
    db.commit()
    # ── Activity log ──────────────────────────────────────────────────────
    try:
        log = SystemLog(
            action="Employee Deleted",
            entity_type="HREmployee",
            entity_name=emp_name,
            details=f"Employee {emp_name} ({emp_code}) deleted by {current_user.name}.",
            changed_fields=f"Deleted: {emp_name} ({emp_code})",
            user=current_user.name,
            workspace="HR",
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        notifications.broadcast(log)
    except Exception:
        pass
        notifications.broadcast(log)
    except Exception:
        pass
    return {"message": f"Employee {emp_name} ({emp_code}) deleted successfully"}


@router.post("/employees/{emp_id}/upload/{doc_type}")
async def upload_employee_doc(
    emp_id: int,
    doc_type: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    valid_types = {"aadhaar", "pan", "bank", "profile", "qualification"}
    if doc_type not in valid_types:
        raise HTTPException(400, f"doc_type must be one of: {valid_types}")
    emp = db.get(HREmployee, emp_id)
    if not emp:
        raise HTTPException(404, "Employee not found")
    content = await read_upload_bytes(file)
    url = save_upload_bytes(content, file.filename or "file", subfolder="hr")
    field_map = {
        "aadhaar": "aadhaar_doc_url",
        "pan": "pan_doc_url",
        "bank": "bank_doc_url",
        "profile": "profile_image_url",
        "qualification": "qualification_doc_url",
    }
    setattr(emp, field_map[doc_type], url)
    emp.updated_by = current_user.name
    db.commit()
    return {"url": url, "doc_type": doc_type}


# ─────────────────────────────────────────────
# Attendance
# ─────────────────────────────────────────────

@router.get("/attendance")
def get_monthly_attendance(
    month: int = Query(...),
    year: int = Query(...),
    location: Optional[str] = Query(None),
    designation: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    days_in_month = calendar.monthrange(year, month)[1]
    q = db.query(HREmployee)
    if location:
        q = q.filter(HREmployee.location.ilike(f"%{location}%"))
    if designation:
        q = q.filter(HREmployee.designation.ilike(f"%{designation}%"))
    if search:
        q = q.filter(HREmployee.name.ilike(f"%{search}%") | HREmployee.employee_code.ilike(f"%{search}%"))
    employees = q.order_by(HREmployee.display_order.asc(), HREmployee.id.asc()).all()

    att_records = db.query(HRAttendance).filter(
        HRAttendance.month == month, HRAttendance.year == year,
    ).all()
    att_map: dict = {}
    ot_map: dict = {}   # employee_id → {day → ot_hours}
    for rec in att_records:
        att_map.setdefault(rec.employee_id, {})[rec.attendance_date.day] = rec.status
        if rec.ot_hours and rec.ot_hours > 0:
            ot_map.setdefault(rec.employee_id, {})[rec.attendance_date.day] = {
                "ot_hours": rec.ot_hours,
                "ot_remarks": rec.ot_remarks,
            }

    close_rec = db.query(HRAttendanceClose).filter(
        HRAttendanceClose.month == month, HRAttendanceClose.year == year,
    ).first()
    is_closed = close_rec.is_closed if close_rec else False

    result = []
    for emp in employees:
        emp_att = att_map.get(emp.id, {})

        # If employee is inactive and requested month/year is strictly after their inactive date, hide them
        inact = getattr(emp, "inactive_date", None)
        if not emp.is_active and inact:
            if ((year > inact.year) or (year == inact.year and month > inact.month)) and not emp_att:
                continue

        counts = {"P": 0, "A": 0, "H": 0, "HD": 0, "HOL": 0, "HP": 0, "PL": 0, "UL": 0, "TR": 0, "WO": 0, "L": 0}
        for s in emp_att.values():
            if s in counts:
                counts[s] += 1
        # Merge legacy "L" into "PL" for clean unified display
        counts["PL"] += counts.get("L", 0)
        # Merge legacy "HP" into "HD" (Half Day No Deduction)
        counts["HD"] += counts.get("HP", 0)
        # Total OT hours for this employee this month
        emp_ot = ot_map.get(emp.id, {})
        total_ot_hours = sum(v["ot_hours"] for v in emp_ot.values())
        result.append({
            "employee_id": emp.id,
            "employee_code": emp.employee_code,
            "name": emp.name,
            "designation": emp.designation,
            "location": emp.location,
            "is_active": emp.is_active,
            "inactive_date": inact.isoformat() if inact else None,
            "attendance": emp_att,
            "ot": emp_ot,
            "total_ot_hours": total_ot_hours if "total_ot_hours" in dir() else 0,
            "summary": counts,
        })

    return {
        "month": month,
        "year": year,
        "days_in_month": days_in_month,
        "is_closed": is_closed,
        "closed_by": close_rec.closed_by if close_rec and close_rec.is_closed else None,
        "closed_at": close_rec.closed_at.isoformat() if close_rec and close_rec.closed_at else None,
        "employees": result,
    }


@router.post("/attendance/mark")
def mark_attendance(
    payload: AttendanceMark,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if payload.attendance_date > date.today():
        raise HTTPException(400, "Future dates ki attendance mark nahi ki ja sakti. Sirf aaj ya pichli dates allow hain.")
    if payload.status in ("CLEAR", "", "NONE"):
        existing = db.query(HRAttendance).filter(
            HRAttendance.employee_id == payload.employee_id,
            HRAttendance.attendance_date == payload.attendance_date,
        ).first()
        if existing:
            db.delete(existing)
            db.commit()
        return {"success": True, "employee_id": payload.employee_id,
                "date": payload.attendance_date.isoformat(), "status": None}
    if payload.status not in VALID_STATUSES:
        raise HTTPException(400, f"Invalid status. Use: {sorted(VALID_STATUSES)}")
    close_rec = db.query(HRAttendanceClose).filter(
        HRAttendanceClose.month == payload.attendance_date.month,
        HRAttendanceClose.year == payload.attendance_date.year,
    ).first()
    if close_rec and close_rec.is_closed:
        raise HTTPException(403, "Attendance for this month is locked. Please reopen attendance to make changes.")
    emp = db.get(HREmployee, payload.employee_id)
    if not emp:
        raise HTTPException(404, "Employee not found")
    if not emp.is_active:
        raise HTTPException(400, f"Cannot mark attendance: {emp.name} is Inactive.")
    existing = db.query(HRAttendance).filter(
        HRAttendance.employee_id == payload.employee_id,
        HRAttendance.attendance_date == payload.attendance_date,
    ).first()
    old_status = existing.status if existing else "Unmarked"
    if existing:
        existing.status = payload.status
        existing.updated_by = current_user.name
    else:
        db.add(HRAttendance(
            employee_id=payload.employee_id,
            attendance_date=payload.attendance_date,
            status=payload.status,
            month=payload.attendance_date.month,
            year=payload.attendance_date.year,
            created_by=current_user.name,
        ))
    db.commit()

    # ── Activity log ──────────────────────────────────────────────────────
    try:
        status_names = {
            "P": "Present",
            "A": "Absent",
            "H": "Half Day (Deducted)",
            "HD": "Half Day (No Deduction)",
            "HOL": "Holiday",
            "HP": "Half Day (No Deduction)",
            "PL": "Paid Leave",
            "L": "Paid Leave",
            "UL": "Unpaid Leave",
            "TR": "Travel/Tour",
            "WO": "Weekly Off",
        }
        new_name = status_names.get(payload.status, payload.status)
        old_name = status_names.get(old_status, old_status)
        if old_status != payload.status:
            dt_str = payload.attendance_date.strftime("%d-%b-%Y")
            log = SystemLog(
                action="Attendance Marked",
                entity_type="HRAttendance",
                entity_name=emp.name,
                details=f"Attendance for {emp.name} ({emp.employee_code}) on {dt_str} marked as {new_name} (was {old_name}) by {current_user.name}.",
                changed_fields=f"{dt_str}: {old_name} → {new_name}",
                user=current_user.name,
                workspace="HR",
            )
            db.add(log)
            db.commit()
            db.refresh(log)
            notifications.broadcast(log)
    except Exception:
        pass

    return {"success": True, "employee_id": payload.employee_id,
            "date": payload.attendance_date.isoformat(), "status": payload.status}


@router.post("/attendance/bulk-mark")
def bulk_mark_attendance(
    payload: BulkAttendanceMark,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    active_emp_ids = {e.id for e in db.query(HREmployee.id).filter(HREmployee.is_active == True).all()}
    valid_records = []
    for rec in payload.records:
        if rec.attendance_date > date.today():
            continue  # Future dates are skipped
        if rec.employee_id not in active_emp_ids:
            continue  # Inactive employees are skipped
        if rec.status not in VALID_STATUSES:
            raise HTTPException(400, f"Invalid status '{rec.status}'")
        valid_records.append(rec)

    for rec in valid_records:
        existing = db.query(HRAttendance).filter(
            HRAttendance.employee_id == rec.employee_id,
            HRAttendance.attendance_date == rec.attendance_date,
        ).first()
        if existing:
            existing.status = rec.status
            existing.updated_by = current_user.name
        else:
            db.add(HRAttendance(
                employee_id=rec.employee_id,
                attendance_date=rec.attendance_date,
                status=rec.status,
                month=rec.attendance_date.month,
                year=rec.attendance_date.year,
                created_by=current_user.name,
            ))
    db.commit()

    # ── Activity log ──────────────────────────────────────────────────────
    try:
        if valid_records:
            log = SystemLog(
                action="Bulk Attendance Marked",
                entity_type="HRAttendance",
                entity_name="Multiple Employees",
                details=f"Bulk attendance marked for {len(valid_records)} record(s) by {current_user.name}.",
                changed_fields=f"{len(valid_records)} attendance records updated",
                user=current_user.name,
                workspace="HR",
            )
            db.add(log)
            db.commit()
            db.refresh(log)
            notifications.broadcast(log)
    except Exception:
        pass

    return {"success": True, "count": len(valid_records)}


@router.get("/attendance/close-status")
def attendance_close_status(
    month: int = Query(...),
    year: int = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rec = db.query(HRAttendanceClose).filter(
        HRAttendanceClose.month == month, HRAttendanceClose.year == year,
    ).first()
    if not rec:
        return {"month": month, "year": year, "is_closed": False}
    return {
        "month": month, "year": year,
        "is_closed": rec.is_closed,
        "closed_by": rec.closed_by,
        "closed_at": rec.closed_at.isoformat() if rec.closed_at else None,
        "reopened_by": rec.reopened_by,
        "reopened_at": rec.reopened_at.isoformat() if rec.reopened_at else None,
        "notes": getattr(rec, "notes", None),
    }


@router.post("/attendance/close")
def close_attendance(
    payload: AttendanceCloseRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rec = db.query(HRAttendanceClose).filter(
        HRAttendanceClose.month == payload.month, HRAttendanceClose.year == payload.year,
    ).first()
    if rec and rec.is_closed:
        raise HTTPException(400, "Attendance already closed for this month.")
    if not rec:
        rec = HRAttendanceClose(month=payload.month, year=payload.year)
        db.add(rec)
    rec.is_closed = True
    rec.closed_by = current_user.name
    rec.closed_at = datetime.utcnow()
    rec.notes = payload.notes
    db.commit()

    # ── Activity log ──────────────────────────────────────────────────────
    try:
        MONTH_NAMES = ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
        m_name = MONTH_NAMES[payload.month] if 1 <= payload.month <= 12 else str(payload.month)
        log = SystemLog(
            action="Attendance Closed",
            entity_type="HRAttendanceClose",
            entity_name=f"{m_name} {payload.year}",
            details=f"Attendance for {m_name} {payload.year} locked and closed by {current_user.name}." + (f" Notes: {payload.notes}" if payload.notes else ""),
            changed_fields=f"Status: Closed/Locked ({m_name} {payload.year})",
            user=current_user.name,
            workspace="HR",
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        notifications.broadcast(log)
    except Exception:
        pass

    return {"success": True, "message": f"Attendance for {payload.month}/{payload.year} closed."}


@router.post("/attendance/reopen")
def reopen_attendance(
    payload: AttendanceCloseRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rec = db.query(HRAttendanceClose).filter(
        HRAttendanceClose.month == payload.month, HRAttendanceClose.year == payload.year,
    ).first()
    if not rec or not rec.is_closed:
        raise HTTPException(400, "Attendance is not closed for this month.")
    rec.is_closed = False
    rec.reopened_by = current_user.name
    rec.reopened_at = datetime.utcnow()
    rec.notes = payload.notes or f"Reopened by {current_user.name}"
    db.commit()

    # ── Activity log ──────────────────────────────────────────────────────
    try:
        MONTH_NAMES = ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
        m_name = MONTH_NAMES[payload.month] if 1 <= payload.month <= 12 else str(payload.month)
        log = SystemLog(
            action="Attendance Reopened",
            entity_type="HRAttendanceClose",
            entity_name=f"{m_name} {payload.year}",
            details=f"Attendance for {m_name} {payload.year} reopened by {current_user.name}." + (f" Notes: {payload.notes}" if payload.notes else ""),
            changed_fields=f"Status: Reopened ({m_name} {payload.year})",
            user=current_user.name,
            workspace="HR",
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        notifications.broadcast(log)
    except Exception:
        pass

    return {"success": True, "message": f"Attendance for {payload.month}/{payload.year} reopened."}


# ─────────────────────────────────────────────
# OT (Overtime) – Save & Fetch
# ─────────────────────────────────────────────

class OTMarkRequest(BaseModel):
    employee_id: int
    attendance_date: date
    ot_hours: float
    ot_remarks: Optional[str] = None


@router.post("/attendance/ot")
def mark_ot(
    payload: OTMarkRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Save/update overtime hours for a specific employee & date.
    Does NOT change the existing attendance status (P/A/TR etc.).
    If no attendance record exists for that date, creates one (status kept as-is or we raise error).
    """
    if payload.attendance_date > date.today():
        raise HTTPException(400, "Future dates ke liye OT mark nahi kar sakte.")

    if payload.ot_hours < 0:
        raise HTTPException(400, "OT hours negative nahi ho sakti.")

    emp = db.get(HREmployee, payload.employee_id)
    if not emp:
        raise HTTPException(404, "Employee not found")
    if not emp.is_active:
        raise HTTPException(400, f"Cannot mark OT: {emp.name} is Inactive.")

    # Check if month is locked
    close_rec = db.query(HRAttendanceClose).filter(
        HRAttendanceClose.month == payload.attendance_date.month,
        HRAttendanceClose.year == payload.attendance_date.year,
    ).first()
    if close_rec and close_rec.is_closed:
        raise HTTPException(403, "Attendance for this month is locked. Please reopen to add OT.")

    existing = db.query(HRAttendance).filter(
        HRAttendance.employee_id == payload.employee_id,
        HRAttendance.attendance_date == payload.attendance_date,
    ).first()

    if existing:
        # Update only OT fields — do NOT touch status
        existing.ot_hours = payload.ot_hours if payload.ot_hours > 0 else None
        existing.ot_remarks = payload.ot_remarks or None
        existing.updated_by = current_user.name
    else:
        # Create a new record with no status (unmarked) but with OT hours
        db.add(HRAttendance(
            employee_id=payload.employee_id,
            attendance_date=payload.attendance_date,
            status="P",   # default to Present for OT days
            month=payload.attendance_date.month,
            year=payload.attendance_date.year,
            ot_hours=payload.ot_hours if payload.ot_hours > 0 else None,
            ot_remarks=payload.ot_remarks or None,
            created_by=current_user.name,
        ))

    db.commit()

    # Activity log
    try:
        dt_str = payload.attendance_date.strftime("%d-%b-%Y")
        log = SystemLog(
            action="OT Marked",
            entity_type="HRAttendance",
            entity_name=emp.name,
            details=f"OT {payload.ot_hours} hrs for {emp.name} ({emp.employee_code}) on {dt_str} by {current_user.name}. Remarks: {payload.ot_remarks or '—'}",
            changed_fields=f"{dt_str}: OT = {payload.ot_hours} hrs",
            user=current_user.name,
            workspace="HR",
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        notifications.broadcast(log)
    except Exception:
        pass

    return {
        "success": True,
        "employee_id": payload.employee_id,
        "date": payload.attendance_date.isoformat(),
        "ot_hours": payload.ot_hours,
    }


@router.delete("/attendance/ot")
def clear_ot(
    employee_id: int = Query(...),
    attendance_date: date = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Clear OT hours for a specific employee & date."""
    existing = db.query(HRAttendance).filter(
        HRAttendance.employee_id == employee_id,
        HRAttendance.attendance_date == attendance_date,
    ).first()
    if existing:
        existing.ot_hours = None
        existing.ot_remarks = None
        existing.updated_by = current_user.name
        db.commit()
    return {"success": True}




@router.get("/salary/config")
def get_salary_config(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _get_config(db)


@router.put("/salary/config")
def update_salary_config(
    payload: SalaryConfigUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    for key, value in payload.configs.items():
        row = db.query(HRSalaryConfig).filter(HRSalaryConfig.config_key == key).first()
        if row:
            row.config_value = str(value)
            row.updated_by = current_user.name
        else:
            db.add(HRSalaryConfig(config_key=key, config_value=str(value), updated_by=current_user.name))
    db.commit()

    # ── Activity log ──────────────────────────────────────────────────────
    try:
        cfg_summary = ", ".join(f"{k}: {v}" for k, v in payload.configs.items())
        log = SystemLog(
            action="Salary Config Updated",
            entity_type="HRSalaryConfig",
            entity_name="Salary Rules",
            details=f"Salary configuration updated by {current_user.name}: {cfg_summary}.",
            changed_fields=cfg_summary,
            user=current_user.name,
            workspace="HR",
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        notifications.broadcast(log)
    except Exception:
        pass

    return _get_config(db)


# ─────────────────────────────────────────────
# Salary
# ─────────────────────────────────────────────

def _calculate_salary(emp: HREmployee, month: int, year: int, cfg: dict, db: Session, overrides: dict) -> dict:
    import calendar as cal_module
    days_in_month = cal_module.monthrange(year, month)[1]

    # Divisor for per-day calculation: default to total calendar days in that month
    raw_working_days = cfg.get("working_days_per_month")
    try:
        cfg_val = int(raw_working_days) if raw_working_days else days_in_month
        # If configured as 30 or 31 (calendar month mode), adapt to the actual days in current month
        if cfg_val in (30, 31):
            working_days = days_in_month
        elif cfg_val > 0:
            working_days = cfg_val
        else:
            working_days = days_in_month
    except (ValueError, TypeError):
        working_days = days_in_month

    half_day_factor = float(cfg.get("half_day_factor", 0.5))

    att_records = db.query(HRAttendance).filter(
        HRAttendance.employee_id == emp.id,
        HRAttendance.month == month,
        HRAttendance.year == year,
    ).all()

    present = sum(1 for a in att_records if a.status == "P")
    travel = sum(1 for a in att_records if a.status == "TR")
    absent = sum(1 for a in att_records if a.status == "A")
    half = sum(1 for a in att_records if a.status == "H")
    half_paid = sum(1 for a in att_records if a.status in ("HD", "HP"))
    paid_leave = sum(1 for a in att_records if a.status in ("L", "PL"))
    unpaid_leave = sum(1 for a in att_records if a.status == "UL")
    wo = sum(1 for a in att_records if a.status == "WO")
    hd = sum(1 for a in att_records if a.status in ("HOL",))

    # IMPORTANT:
    # 1) Weekly Off (WO), Holiday (HD), Paid Leave (PL/L), Travel (TR), and Half Day (Paid/HP) are 100% PAID (no salary cut).
    # 2) Travel (TR) counts as Present on official tour/duty.
    # 3) Half Day (H) is paid half_day_factor (0.5), remainder (0.5) is deducted.
    # 4) Half Day (HP) is 100% paid (no deduction).
    # 5) Unpaid Leave (UL) and Absent (A) have full day salary deducted (paise katenge).
    total_present_days = round(present + travel + paid_leave + half_paid + half * half_day_factor, 1)
    total_paid_days = round(present + travel + paid_leave + wo + hd + half_paid + half * half_day_factor, 1)

    # Detect unmarked/missing days in the month (e.g. joined mid-month or unrecorded dates)
    total_marked = present + travel + absent + half + half_paid + paid_leave + unpaid_leave + wo + hd
    unmarked_days = max(0, days_in_month - total_marked)

    present_salary = float(emp.present_salary or 0)
    # Per day salary based on actual calendar days in the salary month
    per_day = int(round(present_salary / days_in_month)) if days_in_month else 0

    overtime = float(overrides.get("overtime_amount") or 0)
    allowances = float(overrides.get("allowances") or 0)   # kept for legacy/bonus use
    bonus = float(overrides.get("bonus") or 0)

    # Deductions summary for information display:
    absent_deduction = int(round(per_day * absent))
    unpaid_leave_deduction = int(round(per_day * unpaid_leave))
    half_deduction = int(round(per_day * half * (1 - half_day_factor)))
    unmarked_deduction = int(round(per_day * unmarked_days))
    salary_deduction = int(round(absent_deduction + unpaid_leave_deduction + half_deduction + unmarked_deduction))

    # ── EXCEL SALARY SHEET CALCULATION LOGIC ─────────────────────────────
    # 1. Monthly Component Calculations
    # Medical Allowance = 5% of Present Salary
    monthly_medical = int(round(present_salary * 0.05))

    # Basic Salary & CONV (Conveyance Allowance) & HRA:
    # If Present Salary > 25,000:
    #   Basic = 50% of Present Salary
    #   HRA = 25% of Present Salary
    #   CONV = 20% of Present Salary
    # If Present Salary <= 25,000:
    #   Basic = Fixed ₹12,750
    #   CONV = 10% of Present Salary
    #   HRA = Present Salary - Basic Salary - CONV - Medical Allowance
    if present_salary > 25000:
        monthly_conveyance = int(round(present_salary * 0.20))
        monthly_basic = int(round(present_salary * 0.50))
        monthly_hra = int(round(present_salary * 0.25))
    else:
        monthly_conveyance = int(round(present_salary * 0.10))
        monthly_basic = 12750 if present_salary > 0 else 0
        calc_hra = int(round(present_salary - monthly_basic - monthly_conveyance - monthly_medical))
        monthly_hra = max(0, calc_hra)

    monthly_gross = int(round(monthly_basic + monthly_hra + monthly_conveyance + monthly_medical))

    # 2. Attendance-Based Payable Calculation
    # Formula: Payable Component = Monthly Component * Paid Days / Total Days in Salary Month
    attendance_ratio = (total_paid_days / days_in_month) if days_in_month > 0 else 0.0

    # Payable components (can be overridden by user if custom amount entered)
    if overrides.get("basic_amount") is not None:
        payable_basic = int(round(float(overrides["basic_amount"])))
    else:
        payable_basic = int(round(monthly_basic * attendance_ratio))

    if overrides.get("hra_amount") is not None:
        payable_hra = int(round(float(overrides["hra_amount"])))
    else:
        payable_hra = int(round(monthly_hra * attendance_ratio))

    if overrides.get("conveyance_amount") is not None:
        payable_conveyance = int(round(float(overrides["conveyance_amount"])))
    else:
        payable_conveyance = int(round(monthly_conveyance * attendance_ratio))

    if overrides.get("medical_amount") is not None:
        payable_medical = int(round(float(overrides["medical_amount"])))
    else:
        payable_medical = int(round(monthly_medical * attendance_ratio))

    # Gross Salary = Basic Salary + HRA + CONV + Medical Allowance
    gross_salary = int(round(payable_basic + payable_hra + payable_conveyance + payable_medical))

    # Travel Allowance (additional earning)
    if overrides.get("travel_allowance_amount") is not None:
        travel_allowance_amount = int(round(float(overrides["travel_allowance_amount"])))
    else:
        travel_rate = float(cfg.get("travel_allowance_rate", 0))
        travel_allowance_amount = int(round(travel * travel_rate))

    # OT Amount Calculation:
    # Formula: (Monthly Salary / Days in Month / OT Hours Divisor) × Approved OT Hours
    # OT Hours Divisor = configurable (default 12 hours/day)
    total_ot_hours = 0.0
    if overrides.get("overtime_amount") is not None and float(overrides["overtime_amount"] or 0) > 0:
        # Manual override from salary modal
        overtime = int(round(float(overrides["overtime_amount"])))
    else:
        # Auto-calculate from attendance OT hours
        ot_hours_divisor = float(cfg.get("ot_hours_divisor", 12))
        # Fetch total OT hours for this employee & month from attendance records
        ot_records = db.query(HRAttendance).filter(
            HRAttendance.employee_id == emp.id,
            HRAttendance.month == month,
            HRAttendance.year == year,
            HRAttendance.ot_hours != None,
        ).all()
        total_ot_hours = sum(float(r.ot_hours or 0) for r in ot_records)
        if total_ot_hours > 0 and days_in_month > 0 and ot_hours_divisor > 0:
            overtime = int(round((present_salary / days_in_month / ot_hours_divisor) * total_ot_hours))
        else:
            overtime = 0

    # Total Earnings = Gross + OT + TA
    total_earnings = int(round(gross_salary + overtime + travel_allowance_amount))

    # 3. Deductions:
    # PF = 12% of Payable Basic Salary (or configured pf_percentage)
    # PF Deduction Logic:
    # - If monthly Basic Salary > ₹25,000 → Fixed ₹3,000
    # - If monthly Basic Salary ≤ ₹25,000 → 12% of payable Basic Salary
    if overrides.get("pf_deduction") is not None:
        pf_deduction = int(round(float(overrides["pf_deduction"])))
    else:
        if monthly_basic > 25000:
            pf_deduction = 3000
        else:
            pf_deduction = int(round(payable_basic * 12 / 100))

    # ESI = 0.75% of Gross Earned Salary (always applied)
    if overrides.get("esi_deduction") is not None:
        esi_deduction = int(round(float(overrides["esi_deduction"])))
    else:
        esi_deduction = int(round(gross_salary * 0.75 / 100))

    # TDS on Gross Salary (or user override)
    if overrides.get("tds_deduction") is not None:
        tds_deduction = int(round(float(overrides["tds_deduction"])))
    else:
        tds_pct = float(cfg.get("tds_percentage", 0))
        tds_deduction = int(round(gross_salary * tds_pct / 100))

    advance = int(round(float(overrides.get("advance_deduction") or 0)))
    other = int(round(float(overrides.get("other_deductions") or 0)))

    # Total deductions = PF + ESI + Advance + TDS + Other
    total_deductions = int(round(pf_deduction + esi_deduction + advance + tds_deduction + other))
    net = int(round(max(0, total_earnings - total_deductions)))

    return {
        "present_salary": present_salary,
        "total_working_days": working_days,
        "days_in_month": days_in_month,
        "present_days": present,
        "travel_days": travel,
        "paid_leave_days": paid_leave,
        "unpaid_leave_days": unpaid_leave,
        "absent_days": absent,
        "half_days": half,
        "weekly_offs": wo,
        "holidays": hd,
        "total_present_days": total_present_days,
        "total_paid_days": total_paid_days,
        "unmarked_days": unmarked_days,
        "per_day_salary": per_day,
        # Monthly Components (Structure)
        "monthly_basic": monthly_basic,
        "monthly_hra": monthly_hra,
        "monthly_conveyance": monthly_conveyance,
        "monthly_medical": monthly_medical,
        "monthly_gross": monthly_gross,
        # Payable Components (Attendance-adjusted)
        "base_salary": payable_basic,
        "basic_salary": payable_basic,
        "hra": payable_hra,
        "conveyance": payable_conveyance,
        "conveyance_amount": payable_conveyance,
        "medical_allowance": payable_medical,
        "medical_amount": payable_medical,
        "gross_salary": gross_salary,
        "overtime_amount": overtime,
        "total_ot_hours": total_ot_hours if "total_ot_hours" in dir() else 0,
        "travel_allowance": travel_allowance_amount,
        "total_earnings": total_earnings,
        # Deductions
        "allowances": allowances,
        "bonus": bonus,
        "absent_deduction": absent_deduction,
        "unpaid_leave_deduction": unpaid_leave_deduction,
        "half_deduction": half_deduction,
        "unmarked_deduction": unmarked_deduction,
        "salary_deduction": 0.0,
        "attendance_deduction_info": salary_deduction,
        "advance_deduction": advance,
        "other_deductions": other,
        "pf_deduction": pf_deduction,
        "esi_deduction": esi_deduction,
        "tds_deduction": tds_deduction,
        "total_deductions": total_deductions,
        "net_salary": net,
    }


def _salary_to_dict(s: HRSalary) -> dict:
    d = {
        "id": s.id,
        "employee_id": s.employee_id,
        "month": s.month,
        "year": s.year,
        "present_salary": float(s.present_salary or 0),
        "total_working_days": s.total_working_days,
        "present_days": float(s.present_days or 0),
        "paid_leave_days": float(s.paid_leave_days or 0),
        "unpaid_leave_days": float(s.unpaid_leave_days or 0),
        "absent_days": float(s.absent_days or 0),
        "half_days": float(s.half_days or 0),
        "weekly_offs": float(s.weekly_offs or 0),
        "holidays": float(s.holidays or 0),
        "basic_salary": float(s.basic_salary or 0),
        "hra": float(s.hra or 0),
        "conveyance": float(s.conveyance or 0),
        "medical_allowance": float(s.medical_allowance or 0),
        "overtime_amount": float(s.overtime_amount or 0),
        "allowances": float(s.allowances or 0),
        "bonus": float(s.bonus or 0),
        "travel_allowance": float(s.travel_allowance or 0),
        "total_earnings": float(s.total_earnings or 0),
        "gross_salary": float(s.gross_salary or 0),
        "salary_deduction": float(s.salary_deduction or 0),
        "advance_deduction": float(s.advance_deduction or 0),
        "pf_deduction": float(s.pf_deduction or 0),
        "esi_deduction": float(s.esi_deduction or 0),
        "tds_deduction": float(s.tds_deduction or 0),
        "other_deductions": float(s.other_deductions or 0),
        "total_deductions": float(s.total_deductions or 0),
        "net_salary": float(s.net_salary or 0),
        "payment_status": s.payment_status,
        "payment_date": s.payment_date.isoformat() if s.payment_date else None,
        "payment_mode": s.payment_mode,
        "transaction_ref": s.transaction_ref,
        "payment_proof_url": s.payment_proof_url,
        "remarks": s.remarks,
        "generated_by": s.generated_by,
        "approved_by": s.approved_by,
        "paid_by": s.paid_by,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }
    if s.employee:
        d["employee_name"] = s.employee.name
        d["employee_code"] = s.employee.employee_code
        d["designation"] = s.employee.designation
        d["location"] = s.employee.location
        d["bank_account_number"] = s.employee.bank_account_number or ""
        d["bank_ifsc"] = s.employee.bank_ifsc or ""
        d["bank_name"] = s.employee.bank_name or ""
        d["display_order"] = getattr(s.employee, "display_order", 0) or 0
    return d


class SalaryPreviewRequest(BaseModel):
    employee_id: int
    month: int
    year: int
    overtime_amount: Optional[float] = 0
    allowances: Optional[float] = 0
    bonus: Optional[float] = 0
    advance_deduction: Optional[float] = 0
    pf_deduction: Optional[float] = None
    esi_deduction: Optional[float] = None
    tds_deduction: Optional[float] = None
    other_deductions: Optional[float] = 0
    travel_allowance_amount: Optional[float] = 0
    hra_amount: Optional[float] = None
    conveyance_amount: Optional[float] = None
    medical_amount: Optional[float] = None
    force_recalc: bool = False


@router.post("/salary/preview")
def preview_salary(
    payload: SalaryPreviewRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Calculate salary for preview without saving. Works even if attendance is not closed.
    Returns full calculation breakdown + employee info + attendance summary.
    """
    emp = db.get(HREmployee, payload.employee_id)
    if not emp:
        raise HTTPException(404, "Employee not found")

    import calendar as cal_module
    days_in_month = cal_module.monthrange(payload.year, payload.month)[1]

    att_records = db.query(HRAttendance).filter(
        HRAttendance.employee_id == payload.employee_id,
        HRAttendance.month == payload.month,
        HRAttendance.year == payload.year,
    ).all()

    present = sum(1 for a in att_records if a.status == "P")
    travel = sum(1 for a in att_records if a.status == "TR")
    absent = sum(1 for a in att_records if a.status == "A")
    half = sum(1 for a in att_records if a.status == "H")
    half_paid = sum(1 for a in att_records if a.status in ("HD", "HP"))
    paid_leave = sum(1 for a in att_records if a.status in ("L", "PL"))
    unpaid_leave = sum(1 for a in att_records if a.status == "UL")
    wo = sum(1 for a in att_records if a.status == "WO")
    hd = sum(1 for a in att_records if a.status in ("HOL",))

    cfg = _get_config(db)
    overrides = {
        "overtime_amount": payload.overtime_amount,
        "allowances": payload.allowances,
        "bonus": payload.bonus,
        "advance_deduction": payload.advance_deduction,
        "pf_deduction": payload.pf_deduction,
        "esi_deduction": payload.esi_deduction,
        "tds_deduction": payload.tds_deduction,
        "other_deductions": payload.other_deductions,
        "travel_allowance_amount": payload.travel_allowance_amount,
        "hra_amount": payload.hra_amount,
        "conveyance_amount": payload.conveyance_amount,
        "medical_amount": payload.medical_amount,
    }
    calc = _calculate_salary(emp, payload.month, payload.year, cfg, db, overrides)

    # Check if salary already exists for this employee/month/year
    existing_salary = db.query(HRSalary).filter(
        HRSalary.employee_id == payload.employee_id,
        HRSalary.month == payload.month,
        HRSalary.year == payload.year,
    ).first()

    if existing_salary and not payload.force_recalc and payload.hra_amount is None and payload.conveyance_amount is None and payload.medical_amount is None:
        # If salary already saved and no custom overrides passed in preview, reflect the actual saved values
        calc["hra"] = float(existing_salary.hra if existing_salary.hra is not None else 0)
        calc["conveyance"] = float(existing_salary.conveyance if existing_salary.conveyance is not None else 0)
        calc["conveyance_amount"] = calc["conveyance"]
        calc["medical_allowance"] = float(existing_salary.medical_allowance if existing_salary.medical_allowance is not None else 0)
        calc["medical_amount"] = calc["medical_allowance"]
        calc["gross_salary"] = float(existing_salary.gross_salary or 0)
        calc["overtime_amount"] = float(existing_salary.overtime_amount or 0)
        calc["travel_allowance"] = float(existing_salary.travel_allowance or 0)
        calc["total_earnings"] = float(existing_salary.total_earnings or 0)
        calc["pf_deduction"] = float(existing_salary.pf_deduction or 0)
        calc["esi_deduction"] = float(existing_salary.esi_deduction or 0)
        calc["tds_deduction"] = float(existing_salary.tds_deduction or 0)
        calc["advance_deduction"] = float(existing_salary.advance_deduction or 0)
        calc["other_deductions"] = float(existing_salary.other_deductions or 0)
        calc["total_deductions"] = float(existing_salary.total_deductions or 0)
        calc["net_salary"] = float(existing_salary.net_salary or 0)

    close_rec = db.query(HRAttendanceClose).filter(
        HRAttendanceClose.month == payload.month,
        HRAttendanceClose.year == payload.year,
    ).first()

    MONTH_NAMES = ["", "January", "February", "March", "April", "May", "June",
                   "July", "August", "September", "October", "November", "December"]

    return {
        "employee": {
            "id": emp.id,
            "employee_code": emp.employee_code,
            "name": emp.name,
            "designation": emp.designation or "",
            "date_of_joining": emp.date_of_joining.isoformat() if emp.date_of_joining else None,
            "starting_salary": float(emp.starting_salary or 0),
            "present_salary": float(emp.present_salary or 0),
            "location": emp.location or "",
        },
        "period": {
            "month": payload.month,
            "year": payload.year,
            "month_name": MONTH_NAMES[payload.month],
            "days_in_month": days_in_month,
        },
        "attendance_summary": {
            "total_calendar_days": days_in_month,
            "present": present,
            "travel": travel,
            "absent": absent,
            "half_day": half,
            "half_day_paid": half_paid,
            "paid_leave": paid_leave,
            "unpaid_leave": unpaid_leave,
            "weekly_off": wo,
            "holiday": hd,
            "total_present_days": calc["total_present_days"],
            "total_paid_days": calc["total_paid_days"],
        },
        "calculation": calc,
        "salary_config": cfg,
        "existing_salary_id": existing_salary.id if existing_salary else None,
        "existing_salary_status": existing_salary.payment_status if existing_salary else None,
        "existing_salary_data": _salary_to_dict(existing_salary) if existing_salary else None,
        "is_attendance_closed": bool(close_rec and close_rec.is_closed),
    }


@router.post("/salary/generate", status_code=201)
def generate_salary(
    payload: SalaryGenerateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    emp = db.get(HREmployee, payload.employee_id)
    if not emp:
        raise HTTPException(404, "Employee not found")
    if not emp.is_active:
        raise HTTPException(400, "Cannot generate salary for inactive employee.")

    existing = db.query(HRSalary).filter(
        HRSalary.employee_id == payload.employee_id,
        HRSalary.month == payload.month,
        HRSalary.year == payload.year,
    ).first()
    if existing:
        raise HTTPException(400, f"Salary already generated for {emp.name} ({payload.month}/{payload.year}).")

    cfg = _get_config(db)
    overrides = {
        "overtime_amount": payload.overtime_amount,
        "allowances": payload.allowances,
        "bonus": payload.bonus,
        "advance_deduction": payload.advance_deduction,
        "pf_deduction": payload.pf_deduction,
        "esi_deduction": payload.esi_deduction,
        "tds_deduction": payload.tds_deduction,
        "other_deductions": payload.other_deductions,
        "travel_allowance_amount": payload.travel_allowance_amount,
        "hra_amount": payload.hra_amount,
        "conveyance_amount": payload.conveyance_amount,
        "medical_amount": payload.medical_amount,
    }
    calc = _calculate_salary(emp, payload.month, payload.year, cfg, db, overrides)

    sal = HRSalary(
        employee_id=payload.employee_id,
        month=payload.month,
        year=payload.year,
        generated_by=current_user.name,
        remarks=payload.remarks,
        **{k: v for k, v in calc.items() if hasattr(HRSalary, k)},
    )
    db.add(sal)
    db.commit()
    db.refresh(sal)

    # ── Activity log ──────────────────────────────────────────────────────
    try:
        MONTH_NAMES = ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
        m_name = MONTH_NAMES[payload.month] if 1 <= payload.month <= 12 else str(payload.month)
        log = SystemLog(
            action="Salary Generated",
            entity_type="HRSalary",
            entity_name=emp.name,
            details=f"Salary for {emp.name} ({emp.employee_code}) generated for {m_name} {payload.year} by {current_user.name}. Net Salary: ₹{float(sal.net_salary or 0):,.2f}.",
            changed_fields=f"Net: ₹{float(sal.net_salary or 0):,.2f}, Basic: ₹{float(sal.basic_salary or 0):,.2f}, Present: {sal.present_days} days",
            user=current_user.name,
            workspace="HR",
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        notifications.broadcast(log)
    except Exception:
        pass

    return _salary_to_dict(sal)


@router.get("/salary")
def list_salaries(
    month: Optional[int] = Query(None),
    year: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    employee_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = db.query(HRSalary).join(HREmployee)
    if month:
        q = q.filter(HRSalary.month == month)
    if year:
        q = q.filter(HRSalary.year == year)
    if status:
        q = q.filter(HRSalary.payment_status == status)
    if employee_id:
        q = q.filter(HRSalary.employee_id == employee_id)
    salaries = q.order_by(HRSalary.year.desc(), HRSalary.month.desc(), HREmployee.display_order.asc(), HREmployee.id.asc()).all()
    return [_salary_to_dict(s) for s in salaries]


@router.get("/salary/{sal_id}")
def get_salary(sal_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    s = db.get(HRSalary, sal_id)
    if not s:
        raise HTTPException(404, "Salary record not found")
    result = _salary_to_dict(s)
    result["audit_logs"] = [
        {"action": a.action, "old_status": a.old_status, "new_status": a.new_status,
         "changed_by": a.changed_by,
         "changed_at": a.changed_at.isoformat() if a.changed_at else None,
         "notes": a.notes}
        for a in s.audit_logs
    ]
    return result


@router.put("/salary/{sal_id}")
def update_salary(
    sal_id: int,
    payload: SalaryUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    s = db.get(HRSalary, sal_id)
    if not s:
        raise HTTPException(404, "Salary record not found")
    if s.payment_status == "Paid":
        raise HTTPException(400, "Cannot edit a paid salary record.")
    for k, v in payload.model_dump(exclude_unset=True).items():
        if v is not None and hasattr(s, k):
            setattr(s, k, v)
    if payload.hra_amount is not None:
        s.hra = payload.hra_amount
    if payload.conveyance_amount is not None:
        s.conveyance = payload.conveyance_amount
    if payload.medical_amount is not None:
        s.medical_allowance = payload.medical_amount
    if payload.travel_allowance_amount is not None:
        s.travel_allowance = payload.travel_allowance_amount

    # Recalculate gross = base+hra+conv+med
    base_sal = float(s.basic_salary or 0)
    gross = int(round(base_sal + float(s.hra or 0) + float(s.conveyance or 0) + float(s.medical_allowance or 0)))
    s.gross_salary = gross
    total_earn = int(round(gross + float(s.overtime_amount or 0) + float(s.travel_allowance or 0)))
    s.total_earnings = total_earn
    s.salary_deduction = 0.0
    total_ded = int(round(float(s.advance_deduction or 0) +
                       float(s.pf_deduction or 0) + float(s.esi_deduction or 0) +
                       float(s.tds_deduction or 0) + float(s.other_deductions or 0)))
    s.total_deductions = total_ded
    s.net_salary = int(round(max(0.0, total_earn - total_ded)))
    db.commit()
    db.refresh(s)

    # ── Activity log ──────────────────────────────────────────────────────
    try:
        emp_name = s.employee.name if s.employee else "Employee"
        MONTH_NAMES = ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
        m_name = MONTH_NAMES[s.month] if 1 <= s.month <= 12 else str(s.month)
        log = SystemLog(
            action="Salary Updated",
            entity_type="HRSalary",
            entity_name=emp_name,
            details=f"Salary for {emp_name} ({m_name} {s.year}) updated by {current_user.name}. Net: ₹{float(s.net_salary or 0):,.2f}, Deductions: ₹{float(s.total_deductions or 0):,.2f}.",
            changed_fields=f"Net: ₹{float(s.net_salary or 0):,.2f}, Gross: ₹{float(s.gross_salary or 0):,.2f}, Ded: ₹{float(s.total_deductions or 0):,.2f}",
            user=current_user.name,
            workspace="HR",
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        notifications.broadcast(log)
    except Exception:
        pass

    return _salary_to_dict(s)


@router.post("/salary/{sal_id}/approve")
def approve_salary(sal_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    s = db.get(HRSalary, sal_id)
    if not s:
        raise HTTPException(404, "Salary record not found")
    old = s.payment_status
    s.payment_status = "Approved"
    s.approved_by = current_user.name
    db.add(HRSalaryAudit(salary_id=s.id, action="approve", old_status=old, new_status="Approved", changed_by=current_user.name))
    db.commit()

    # ── Activity log ──────────────────────────────────────────────────────
    try:
        emp_name = s.employee.name if s.employee else "Employee"
        MONTH_NAMES = ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
        m_name = MONTH_NAMES[s.month] if 1 <= s.month <= 12 else str(s.month)
        log = SystemLog(
            action="Salary Approved",
            entity_type="HRSalary",
            entity_name=emp_name,
            details=f"Salary for {emp_name} ({m_name} {s.year}) approved by {current_user.name}. Amount: ₹{float(s.net_salary or 0):,.2f}.",
            changed_fields=f"Status: {old} → Approved, Net: ₹{float(s.net_salary or 0):,.2f}",
            user=current_user.name,
            workspace="HR",
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        notifications.broadcast(log)
    except Exception:
        pass

    return _salary_to_dict(s)


@router.post("/salary/{sal_id}/mark-paid")
def mark_salary_paid(
    sal_id: int,
    payload: MarkPaidRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    s = db.get(HRSalary, sal_id)
    if not s:
        raise HTTPException(404, "Salary record not found")
    old = s.payment_status
    s.payment_status = "Paid"
    s.payment_date = payload.payment_date
    s.payment_mode = payload.payment_mode
    s.transaction_ref = payload.transaction_ref
    if payload.remarks:
        s.remarks = payload.remarks
    s.paid_by = current_user.name
    db.add(HRSalaryAudit(
        salary_id=s.id, action="mark_paid", old_status=old, new_status="Paid",
        changed_by=current_user.name,
        notes=f"Mode: {payload.payment_mode}, Ref: {payload.transaction_ref}",
    ))
    db.commit()

    # ── Activity log ──────────────────────────────────────────────────────
    try:
        emp_name = s.employee.name if s.employee else "Employee"
        MONTH_NAMES = ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
        m_name = MONTH_NAMES[s.month] if 1 <= s.month <= 12 else str(s.month)
        log = SystemLog(
            action="Salary Paid",
            entity_type="HRSalary",
            entity_name=emp_name,
            details=f"Salary for {emp_name} ({m_name} {s.year}) marked as Paid by {current_user.name}. Amount: ₹{float(s.net_salary or 0):,.2f}. Mode: {payload.payment_mode or '—'}" + (f", Ref: {payload.transaction_ref}" if payload.transaction_ref else ""),
            changed_fields=f"Status: {old} → Paid, Mode: {payload.payment_mode}, Date: {payload.payment_date}",
            user=current_user.name,
            workspace="HR",
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        notifications.broadcast(log)
    except Exception:
        pass

    return _salary_to_dict(s)


@router.post("/salary/{sal_id}/hold")
def hold_salary(sal_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    s = db.get(HRSalary, sal_id)
    if not s:
        raise HTTPException(404, "Salary record not found")
    old = s.payment_status
    s.payment_status = "On Hold"
    db.add(HRSalaryAudit(salary_id=s.id, action="hold", old_status=old, new_status="On Hold", changed_by=current_user.name))
    db.commit()

    # ── Activity log ──────────────────────────────────────────────────────
    try:
        emp_name = s.employee.name if s.employee else "Employee"
        MONTH_NAMES = ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
        m_name = MONTH_NAMES[s.month] if 1 <= s.month <= 12 else str(s.month)
        log = SystemLog(
            action="Salary On Hold",
            entity_type="HRSalary",
            entity_name=emp_name,
            details=f"Salary for {emp_name} ({m_name} {s.year}) put On Hold by {current_user.name}.",
            changed_fields=f"Status: {old} → On Hold",
            user=current_user.name,
            workspace="HR",
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        notifications.broadcast(log)
    except Exception:
        pass

    return _salary_to_dict(s)


@router.delete("/salary/{sal_id}")
def delete_salary(sal_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    s = db.get(HRSalary, sal_id)
    if not s:
        raise HTTPException(404, "Salary record not found")
    emp_name = s.employee.name if s.employee else "Employee"
    sal_month = s.month
    sal_year = s.year
    net_amt = float(s.net_salary or 0)
    db.query(HRSalaryAudit).filter(HRSalaryAudit.salary_id == sal_id).delete()
    db.delete(s)
    db.commit()

    # ── Activity log ──────────────────────────────────────────────────────
    try:
        MONTH_NAMES = ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
        m_name = MONTH_NAMES[sal_month] if 1 <= sal_month <= 12 else str(sal_month)
        log = SystemLog(
            action="Salary Deleted",
            entity_type="HRSalary",
            entity_name=emp_name,
            details=f"Salary record for {emp_name} ({m_name} {sal_year}) deleted by {current_user.name}.",
            changed_fields=f"Deleted salary {m_name} {sal_year} (₹{net_amt:,.2f})",
            user=current_user.name,
            workspace="HR",
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        notifications.broadcast(log)
    except Exception:
        pass

    return {"success": True, "message": "Salary record deleted successfully."}


@router.post("/salary/{sal_id}/upload-proof")
async def upload_salary_proof(
    sal_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    s = db.get(HRSalary, sal_id)
    if not s:
        raise HTTPException(404, "Salary record not found")
    content = await read_upload_bytes(file)
    url = save_upload_bytes(content, file.filename or "proof", subfolder="hr/payments")
    s.payment_proof_url = url
    db.commit()
    return {"url": url}


@router.get("/salary/{sal_id}/slip")
def salary_slip(sal_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    s = db.get(HRSalary, sal_id)
    if not s:
        raise HTTPException(404, "Salary record not found")
    emp = s.employee
    MONTH_NAMES = ["", "January", "February", "March", "April", "May", "June",
                   "July", "August", "September", "October", "November", "December"]
    return {
        "slip": {
            "company_name": "JB Engineering",
            "salary_month": f"{MONTH_NAMES[s.month]} {s.year}",
            "employee_name": emp.name,
            "employee_code": emp.employee_code,
            "designation": emp.designation or "",
            "location": emp.location or "",
            "bank_name": emp.bank_name or "",
            "bank_account_number": emp.bank_account_number or "",
            "bank_ifsc": emp.bank_ifsc or "",
            "date_of_joining": emp.date_of_joining.isoformat() if emp.date_of_joining else "",
            "attendance": {
                "total_working_days": s.total_working_days,
                "present_days": float(s.present_days or 0),
                "paid_leave_days": float(s.paid_leave_days or 0),
                "absent_days": float(s.absent_days or 0),
                "half_days": float(s.half_days or 0),
                "weekly_offs": float(s.weekly_offs or 0),
            },
            "earnings": {
                "basic_salary": float(s.basic_salary or 0),
                "hra": float(s.hra or 0),
                "conveyance": float(s.conveyance or 0),
                "medical_allowance": float(s.medical_allowance or 0),
                "gross_salary": float(s.gross_salary or 0),
                "overtime": float(s.overtime_amount or 0),
                "travel_allowance": float(s.travel_allowance or 0),
                "total_earnings": float(s.total_earnings or 0),
            },
            "deductions": {
                "salary_deduction": float(s.salary_deduction or 0),
                "advance_deduction": float(s.advance_deduction or 0),
                "pf_deduction": float(s.pf_deduction or 0),
                "esi_deduction": float(s.esi_deduction or 0),
                "tds_deduction": float(s.tds_deduction or 0),
                "other_deductions": float(s.other_deductions or 0),
                "total_deductions": float(s.total_deductions or 0),
            },
            "net_salary": float(s.net_salary or 0),
            "payment_status": s.payment_status,
            "payment_date": s.payment_date.isoformat() if s.payment_date else None,
            "payment_mode": s.payment_mode,
            "transaction_ref": s.transaction_ref,
            "remarks": s.remarks,
        }
    }
