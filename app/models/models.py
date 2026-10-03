from sqlalchemy import (
    Column, Integer, String, Float, Numeric, Text, DateTime, Date, Boolean,
    ForeignKey, Enum, func, UniqueConstraint,
)
from sqlalchemy.orm import relationship
from app.database import Base
from typing import List
import enum


class PaymentStatus(str, enum.Enum):
    PENDING = "Pending"
    PARTIAL = "Partial"
    PAID = "Paid"


class DeliveryStatus(str, enum.Enum):
    NOT_DELIVERED = "Not Delivered"
    DELIVERED = "Delivered"
    PARTIAL = "Partial"


class InventoryStatus(str, enum.Enum):
    IN_STOCK = "In Stock"
    LOW_STOCK = "Low Stock"
    OUT_OF_STOCK = "Out of Stock"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    email = Column(String(150), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True)
    # This is intentionally a system-wide capability only.  Application admin
    # rights live in application_access and must never be inferred globally.
    is_super_admin = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    application_access = relationship("ApplicationAccess", back_populates="user", cascade="all, delete-orphan")

    @property
    def is_admin(self) -> bool:
        if getattr(self, "is_super_admin", False):
            return True
        if hasattr(self, "application_access") and self.application_access:
            for acc in self.application_access:
                if getattr(acc, "role", None) == "admin":
                    return True
        return False


class Application(Base):
    __tablename__ = "applications"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False, unique=True)
    code = Column(String(50), nullable=False, unique=True, index=True)
    access = relationship("ApplicationAccess", back_populates="application", cascade="all, delete-orphan")


class ApplicationAccess(Base):
    __tablename__ = "application_access"
    __table_args__ = (UniqueConstraint("user_id", "application_id", name="uq_application_access_user_app"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    application_id = Column(Integer, ForeignKey("applications.id"), nullable=False, index=True)
    # no_access rows are retained so the administrator can manage a complete matrix
    role = Column(String(20), nullable=False, default="none")  # none | user | admin

    user = relationship("User", back_populates="application_access")
    application = relationship("Application", back_populates="access")


class StorePurchaseItem(Base):
    """Inventory belongs to Store Purchase only; it is deliberately separate
    from the legacy Marketing inventory table."""
    __tablename__ = "store_purchase_items"
    id = Column(Integer, primary_key=True)
    # A group is not a unique material by itself: one group can contain many
    # different item names (for example, Round Black / 42mm and / 63mm).
    name = Column(String(300), nullable=False, index=True)
    quantity = Column(Float, nullable=False, default=0)
    reorder_level = Column(Float, nullable=False, default=0)
    vendor_name = Column(String(200), nullable=True)
    reference_no = Column(String(100), nullable=True)
    receipt_date = Column(Date, nullable=True)
    stock_item = Column(String(150), nullable=True)
    uom = Column(String(50), nullable=False, default="Nos")
    location = Column(String(150), nullable=True)
    required_for = Column(String(200), nullable=True)
    rate = Column(Float, nullable=False, default=0)
    previous_quantity = Column(Float, nullable=False, default=0)
    current_month_quantity = Column(Float, nullable=False, default=0)
    total_received_quantity = Column(Float, nullable=False, default=0)
    issued_quantity = Column(Float, nullable=False, default=0)
    status = Column(String(30), nullable=False, default="In Stock")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    transactions = relationship("StorePurchaseStockTransaction", back_populates="item", cascade="all, delete-orphan", order_by="StorePurchaseStockTransaction.transaction_date.desc()")
    stock_items = relationship("StorePurchaseStockItem", back_populates="material", cascade="all, delete-orphan", order_by="StorePurchaseStockItem.name")


class StorePurchaseStockItem(Base):
    """Named stock variants belonging to one material master (one material
    can therefore carry any number of stock items)."""
    __tablename__ = "store_purchase_stock_items"
    __table_args__ = (UniqueConstraint("material_id", "name", name="uq_store_material_stock_item"),)
    id = Column(Integer, primary_key=True)
    material_id = Column(Integer, ForeignKey("store_purchase_items.id"), nullable=False, index=True)
    name = Column(String(150), nullable=False)
    uom = Column(String(50), nullable=False, default="Nos")
    location = Column(String(150), nullable=True)
    vendor_name = Column(String(200), nullable=True)
    rate = Column(Float, nullable=False, default=0)
    total_received_quantity = Column(Float, nullable=False, default=0)
    issued_quantity = Column(Float, nullable=False, default=0)
    available_quantity = Column(Float, nullable=False, default=0)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    material = relationship("StorePurchaseItem", back_populates="stock_items")


class StorePurchaseStockTransaction(Base):
    """Immutable receipt/issue ledger: item quantity is the current balance."""
    __tablename__ = "store_purchase_stock_transactions"
    id = Column(Integer, primary_key=True)
    item_id = Column(Integer, ForeignKey("store_purchase_items.id"), nullable=False, index=True)
    transaction_type = Column(String(20), nullable=False)  # RECEIPT | ISSUE
    quantity = Column(Float, nullable=False)
    rate = Column(Float, nullable=False, default=0)
    amount = Column(Float, nullable=False, default=0)
    transaction_date = Column(Date, nullable=False)
    vendor_name = Column(String(200), nullable=True)
    reference_no = Column(String(100), nullable=True)
    required_for = Column(String(200), nullable=True)
    issued_to = Column(String(200), nullable=True)
    location = Column(String(150), nullable=True)
    remarks = Column(Text, nullable=True)
    receipt_line_id = Column(Integer, ForeignKey("store_purchase_material_receipt_lines.id"), nullable=True, unique=True, index=True)
    created_at = Column(DateTime, server_default=func.now())
    created_by = Column(String(100), nullable=True)
    item = relationship("StorePurchaseItem", back_populates="transactions")


class StorePurchaseStockTransfer(Base):
    """Store-to-store movement. It does not alter total company stock."""
    __tablename__ = "store_purchase_stock_transfers"
    id = Column(Integer, primary_key=True)
    item_id = Column(Integer, ForeignKey("store_purchase_items.id"), nullable=False, index=True)
    quantity = Column(Float, nullable=False)
    from_location = Column(String(150), nullable=False)
    to_location = Column(String(150), nullable=False)
    transfer_date = Column(Date, nullable=False)
    required_for = Column(String(200), nullable=True)
    remarks = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    created_by = Column(String(100), nullable=True)
    item = relationship("StorePurchaseItem")


class StorePurchaseOrder(Base):
    __tablename__ = "store_purchase_orders"
    id = Column(Integer, primary_key=True)
    order_number = Column(String(100), nullable=False, unique=True, index=True)
    supplier = Column(String(200), nullable=False)
    item_name = Column(String(300), nullable=False)
    quantity = Column(Float, nullable=False, default=0)
    unit_price = Column(Float, nullable=False, default=0)
    status = Column(String(30), nullable=False, default="Pending Approval")
    po_type = Column(String(30), nullable=False, default="Regular Vendor PO", server_default="Regular Vendor PO")
    created_at = Column(DateTime, server_default=func.now())
    created_by = Column(String(100), nullable=True)
    details = relationship("StorePurchaseOrderDetail", back_populates="order", uselist=False, cascade="all, delete-orphan")
    line_items = relationship("StorePurchaseOrderLine", back_populates="order", cascade="all, delete-orphan", order_by="StorePurchaseOrderLine.line_number")
    material_receipts = relationship("StorePurchaseMaterialReceipt", back_populates="order", cascade="all, delete-orphan")
    activities = relationship("StorePurchaseOrderActivity", back_populates="order", cascade="all, delete-orphan", order_by="StorePurchaseOrderActivity.created_at.desc()")
    approvals = relationship("StorePurchaseOrderApproval", back_populates="order", cascade="all, delete-orphan", order_by="StorePurchaseOrderApproval.level_order, StorePurchaseOrderApproval.id")


class POApprovalLevel(Base):
    """Super-admin configured, amount-based Store PO approval rule."""
    __tablename__ = "po_approval_levels"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    min_amount = Column(Float, nullable=False, default=0)
    max_amount = Column(Float, nullable=True)  # NULL means no upper limit
    approval_rule = Column(String(10), nullable=False, default="any")  # any | all
    is_enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, server_default=func.now())
    approvers = relationship("POApprovalLevelApprover", back_populates="level", cascade="all, delete-orphan")


class POApprovalLevelApprover(Base):
    __tablename__ = "po_approval_level_approvers"
    __table_args__ = (UniqueConstraint("level_id", "user_id", name="uq_po_level_approver"),)
    id = Column(Integer, primary_key=True)
    level_id = Column(Integer, ForeignKey("po_approval_levels.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    level = relationship("POApprovalLevel", back_populates="approvers")
    user = relationship("User")


class POApproverPermission(Base):
    """Separate from application access: this is the explicit approval authority."""
    __tablename__ = "po_approver_permissions"
    __table_args__ = (UniqueConstraint("user_id", name="uq_po_approver_permission"),)
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    max_approval_amount = Column(Float, nullable=True)  # NULL means unlimited
    is_enabled = Column(Boolean, nullable=False, default=True)
    user = relationship("User")


class StorePurchaseOrderApproval(Base):
    """A snapshot of one assigned approver for a PO; never rewritten as settings change."""
    __tablename__ = "store_purchase_order_approvals"
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("store_purchase_orders.id"), nullable=False, index=True)
    level_id = Column(Integer, ForeignKey("po_approval_levels.id"), nullable=False)
    level_name = Column(String(100), nullable=False)
    level_order = Column(Integer, nullable=False, default=1)
    approval_rule = Column(String(10), nullable=False, default="any")
    approver_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    action = Column(String(20), nullable=False, default="Pending")
    action_at = Column(DateTime, nullable=True)
    rejection_reason = Column(Text, nullable=True)
    order = relationship("StorePurchaseOrder", back_populates="approvals")
    approver = relationship("User")


class StorePurchaseOrderActivity(Base):
    """Immutable audit trail for Store Purchase Orders."""
    __tablename__ = "store_purchase_order_activities"

    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("store_purchase_orders.id"), nullable=False, index=True)
    action = Column(String(50), nullable=False)
    details = Column(String(500), nullable=True)
    performed_by = Column(String(100), nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    order = relationship("StorePurchaseOrder", back_populates="activities")


class StorePurchaseOrderDetail(Base):
    """Vendor-facing PO header, totals and terms.  Kept Store-only so it
    cannot overlap the existing Marketing purchase-order records."""
    __tablename__ = "store_purchase_order_details"

    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("store_purchase_orders.id"), nullable=False, unique=True, index=True)
    vendor_code = Column(String(100), nullable=True)
    vendor_address = Column(Text, nullable=True)
    vendor_contact = Column(String(200), nullable=True)
    vendor_gst = Column(String(50), nullable=True)
    po_date = Column(Date, nullable=True)
    reference = Column(String(300), nullable=True)
    delivery_address = Column(Text, nullable=True)
    gst_rate = Column(Float, nullable=False, default=18)
    subtotal = Column(Float, nullable=False, default=0)
    gst_amount = Column(Float, nullable=False, default=0)
    round_off = Column(Float, nullable=False, default=0)
    grand_total = Column(Float, nullable=False, default=0)
    price_basis = Column(String(300), nullable=True)
    packing_terms = Column(String(300), nullable=True)
    freight_terms = Column(String(300), nullable=True)
    insurance_terms = Column(String(300), nullable=True)
    delivery_terms = Column(Text, nullable=True)
    inspection_terms = Column(String(300), nullable=True)
    warranty_terms = Column(String(300), nullable=True)
    payment_terms = Column(String(300), nullable=True)
    quantity_variance = Column(String(300), nullable=True)
    notes = Column(Text, nullable=True)

    order = relationship("StorePurchaseOrder", back_populates="details")


class StorePurchaseOrderLine(Base):
    __tablename__ = "store_purchase_order_lines"

    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("store_purchase_orders.id"), nullable=False, index=True)
    line_number = Column(Integer, nullable=False)
    item_description = Column(String(500), nullable=False)
    quantity = Column(Float, nullable=False, default=0)
    uom = Column(String(50), nullable=False, default="Nos")
    unit_rate = Column(Float, nullable=False, default=0)
    amount = Column(Float, nullable=False, default=0)

    order = relationship("StorePurchaseOrder", back_populates="line_items")


class StorePurchaseMaterialReceipt(Base):
    """Goods received record that posts its accepted quantities to Inventory."""
    __tablename__ = "store_purchase_material_receipts"

    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("store_purchase_orders.id"), nullable=False, index=True)
    invoice_date = Column(Date, nullable=False)
    invoice_number = Column(String(150), nullable=False)
    freight_charge = Column(Float, nullable=False, default=0)
    bill_file_url = Column(String(500), nullable=True)
    vendor_name = Column(String(200), nullable=False)
    reference = Column(String(300), nullable=True)
    vendor_code = Column(String(100), nullable=True)
    remarks = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    created_by = Column(String(100), nullable=True)
    updated_at = Column(DateTime, nullable=True)
    updated_by = Column(String(100), nullable=True)
    payment_status = Column(String(30), nullable=False, default="Unpaid")
    amount_paid = Column(Float, nullable=True, default=0)
    payment_date = Column(Date, nullable=True)
    payment_reference = Column(String(200), nullable=True)

    order = relationship("StorePurchaseOrder", back_populates="material_receipts")
    lines = relationship("StorePurchaseMaterialReceiptLine", back_populates="receipt", cascade="all, delete-orphan", order_by="StorePurchaseMaterialReceiptLine.line_number")


class StorePurchaseMaterialReceiptLine(Base):
    __tablename__ = "store_purchase_material_receipt_lines"

    id = Column(Integer, primary_key=True)
    receipt_id = Column(Integer, ForeignKey("store_purchase_material_receipts.id"), nullable=False, index=True)
    line_number = Column(Integer, nullable=False)
    item_description = Column(String(500), nullable=False)
    ordered_quantity = Column(Float, nullable=False, default=0)
    received_quantity = Column(Float, nullable=False, default=0)
    uom = Column(String(50), nullable=False, default="Nos")
    unit_rate = Column(Float, nullable=False, default=0)
    amount = Column(Float, nullable=False, default=0)

    receipt = relationship("StorePurchaseMaterialReceipt", back_populates="lines")


class ItemMasterItem(Base):
    __tablename__ = "item_master"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(300), nullable=False, unique=True, index=True)
    created_at = Column(DateTime, server_default=func.now())
    created_by = Column(String(100), nullable=True)
    updated_at = Column(DateTime, nullable=True)
    updated_by = Column(String(100), nullable=True)

    sizes = relationship("ItemMasterSize", back_populates="item", cascade="all, delete-orphan", order_by="ItemMasterSize.id")


class ItemMasterSize(Base):
    __tablename__ = "item_master_sizes"

    id = Column(Integer, primary_key=True, index=True)
    item_id = Column(Integer, ForeignKey("item_master.id"), nullable=False)
    size = Column(String(500), nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    created_by = Column(String(100), nullable=True)

    item = relationship("ItemMasterItem", back_populates="sizes")


class StorePOItemMasterItem(Base):
    """Item master used only by Store Purchase Orders, never by Marketing POs."""
    __tablename__ = "store_po_item_master"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(300), nullable=False, unique=True, index=True)
    created_at = Column(DateTime, server_default=func.now())
    created_by = Column(String(100), nullable=True)
    updated_at = Column(DateTime, nullable=True)
    updated_by = Column(String(100), nullable=True)

    sizes = relationship("StorePOItemMasterSize", back_populates="item", cascade="all, delete-orphan", order_by="StorePOItemMasterSize.id")


class StorePOItemMasterSize(Base):
    __tablename__ = "store_po_item_master_sizes"

    id = Column(Integer, primary_key=True, index=True)
    item_id = Column(Integer, ForeignKey("store_po_item_master.id"), nullable=False)
    size = Column(String(500), nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    created_by = Column(String(100), nullable=True)

    item = relationship("StorePOItemMasterItem", back_populates="sizes")


class WOItemMasterItem(Base):
    __tablename__ = "wo_item_master"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(300), nullable=False, unique=True, index=True)
    created_at = Column(DateTime, server_default=func.now())
    created_by = Column(String(100), nullable=True)
    updated_at = Column(DateTime, nullable=True)
    updated_by = Column(String(100), nullable=True)

    sizes = relationship("WOItemMasterSize", back_populates="item", cascade="all, delete-orphan", order_by="WOItemMasterSize.id")


class WOItemMasterSize(Base):
    __tablename__ = "wo_item_master_sizes"

    id = Column(Integer, primary_key=True, index=True)
    item_id = Column(Integer, ForeignKey("wo_item_master.id"), nullable=False)
    size = Column(String(50), nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    created_by = Column(String(100), nullable=True)

    item = relationship("WOItemMasterItem", back_populates="sizes")


class UOMOption(Base):
    __tablename__ = "uom_options"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, unique=True, index=True)
    created_at = Column(DateTime, server_default=func.now())
    created_by = Column(String(100), nullable=True)
    updated_at = Column(DateTime, nullable=True)
    updated_by = Column(String(100), nullable=True)


class StoreUOMOption(Base):
    """UOM choices maintained exclusively for Store Purchase Orders."""
    __tablename__ = "store_uom_options"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, unique=True, index=True)
    created_at = Column(DateTime, server_default=func.now())
    created_by = Column(String(100), nullable=True)
    updated_at = Column(DateTime, nullable=True)
    updated_by = Column(String(100), nullable=True)


class Client(Base):
    __tablename__ = "clients"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False, index=True)
    location = Column(String(100), nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    purchase_orders = relationship("PurchaseOrder", back_populates="client_rel")
    records = relationship("Record", back_populates="client_rel")
    projects = relationship("Project", back_populates="client_rel", cascade="all, delete-orphan")
    work_orders = relationship("WorkOrder", back_populates="client_rel")


class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(300), nullable=False, index=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    client_rel = relationship("Client", back_populates="projects")
    purchase_orders = relationship("PurchaseOrder", back_populates="project_rel")
    work_orders = relationship("WorkOrder", back_populates="project_rel")


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(300), nullable=False, unique=True, index=True)
    quantity = Column(Integer, default=0, nullable=False)
    status = Column(
        Enum(InventoryStatus),
        default=InventoryStatus.IN_STOCK,
        nullable=False,
    )
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"

    id = Column(Integer, primary_key=True, index=True)
    client_name = Column(String(200), nullable=False, index=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=True)
    po_number = Column(String(100), nullable=False, unique=True, index=True)
    item = Column(String(300), nullable=True, default="")
    uom = Column(String(50), nullable=False, default="Nos")
    project = Column(String(300), nullable=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True)
    location = Column(String(100), nullable=True)
    total_quantity = Column(Float, nullable=False, default=0)
    delivered_quantity = Column(Float, nullable=False, default=0)
    unit_price = Column(Float, nullable=False, default=0)
    gst = Column(String(20), nullable=True, default="18")
    freight = Column(Float, nullable=False, default=0)
    payment_terms = Column(String(100), nullable=True)
    po_date = Column(DateTime, nullable=True)
    validity_date = Column(DateTime, nullable=True)
    file_url = Column(String(500), nullable=True)
    remark = Column(Text, nullable=True)
    short_closed = Column(Boolean, default=False, nullable=False)
    short_closed_at = Column(DateTime, nullable=True)
    short_closed_by = Column(String(100), nullable=True)
    short_closed_remark = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    created_by = Column(String(100), nullable=True)
    last_opened_at = Column(DateTime, nullable=True)
    last_opened_by = Column(String(100), nullable=True)
    last_updated_at = Column(DateTime, nullable=True)
    last_updated_by = Column(String(100), nullable=True)

    # Soft-delete fields
    is_deleted = Column(Boolean, default=False, nullable=False, server_default="0")
    deleted_at = Column(DateTime, nullable=True)
    deleted_by = Column(String(100), nullable=True)
    permanent_delete_at = Column(DateTime, nullable=True)

    client_rel = relationship("Client", back_populates="purchase_orders")
    project_rel = relationship("Project", back_populates="purchase_orders")
    sales = relationship("Sale", back_populates="purchase_order")
    line_items = relationship("POLineItem", back_populates="purchase_order", cascade="all, delete-orphan", order_by="POLineItem.id")

    @property
    def total_qty(self) -> float:
        if self.line_items:
            return sum(li.quantity for li in self.line_items)
        return self.total_quantity  # type: ignore

    @property
    def delivered_qty(self) -> float:
        if self.line_items:
            return sum(li.delivered_quantity for li in self.line_items)
        return self.delivered_quantity  # type: ignore

    @property
    def pending_quantity(self) -> float:
        return round(max(0, self.total_qty - self.delivered_qty), 10)

    @property
    def delivery_status(self) -> str:
        if self.short_closed:
            return "Short Closed"
        d_qty = self.delivered_qty
        t_qty = self.total_qty
        if d_qty <= 0:
            return DeliveryStatus.NOT_DELIVERED
        elif d_qty >= t_qty:
            return DeliveryStatus.DELIVERED
        return DeliveryStatus.PARTIAL
    @property
    def all_dispatches_marked(self) -> bool:
        from sqlalchemy.orm import object_session
        from sqlalchemy import or_, func
        # Sale is defined below, automatically available in globals
        
        # 1. PO must be fully dispatched quantity-wise
        if self.delivery_status != DeliveryStatus.DELIVERED:
            return False
            
        # 2. Find ALL dispatches linked by ID or Number (case-insensitive)
        session = object_session(self)
        po_num_clean = str(self.po_number or "").strip().lower()
        
        if session:
            all_related_sales = session.query(Sale).filter(
                or_(
                    Sale.po_id == self.id,
                    func.lower(func.trim(Sale.po_number)) == po_num_clean
                )
            ).all()
        else:
            all_related_sales = self.sales
            
        # If no sales records exist yet, it's definitely not finished
        if not all_related_sales:
            return False
            
        # 3. Every single dispatch found must be "Delivered" AND have enough challans
        for s in all_related_sales:
            # Check if this sale is marked as Delivered
            if s.delivery_status != "Delivered":
                return False
                
            # COUNT DISPATCH EVENTS: 
            # 1 (initial) + number of "Items Dispatched" activities
            dispatch_count = 1
            if s.activities:
                for act in s.activities:
                    if act.action == "Items Dispatched":
                        dispatch_count += 1
            
            # Check if it has enough valid file URLs (one per dispatch event)
            url = s.delivery_challan_url or ""
            valid_urls = [u for u in url.split(";") if u and u.strip()]
            
            if len(valid_urls) < dispatch_count:
                return False
                
        return True

    @property
    def gst_rate(self) -> float:
        if self.gst is None or str(self.gst).strip() in ("", "0"):
            return 0.0
        if str(self.gst).startswith("₹"):
            return 0.0
        cleaned = str(self.gst).replace("%", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return 0.0

    @property
    def subtotal(self) -> float:
        if self.line_items:
            return sum(li.quantity * li.unit_price for li in self.line_items)
        return self.total_quantity * self.unit_price  # type: ignore

    @property
    def gst_amount(self) -> float:
        # If global GST is set on the PO, use it
        if self.gst and str(self.gst).strip() not in ("", "0"):
            if str(self.gst).startswith("₹"):
                cleaned = str(self.gst).replace("₹", "").replace(",", "").strip()
                try:
                    return float(cleaned)
                except ValueError:
                    return 0.0
            else:
                return self.subtotal * self.gst_rate / 100
        # Otherwise sum per-item GST
        if self.line_items:
            return sum(li.gst_amount for li in self.line_items)
        return self.subtotal * self.gst_rate / 100

    @property
    def grand_total(self) -> float:
        items_freight = sum(li.freight for li in self.line_items) if self.line_items else 0.0
        return self.subtotal + self.gst_amount + self.freight + items_freight  # type: ignore

    @property
    def items_display(self) -> str:
        """Comma-separated item names for display."""
        if self.line_items:
            return ", ".join(li.item for li in self.line_items)
        return self.item or ""  # type: ignore


class POLineItem(Base):
    __tablename__ = "po_line_items"

    id = Column(Integer, primary_key=True, index=True)
    po_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=False)
    item = Column(String(300), nullable=False)
    quantity = Column(Float, nullable=False, default=0)
    delivered_quantity = Column(Float, nullable=False, default=0)
    uom = Column(String(50), nullable=False, default="Nos")
    unit_price = Column(Float, nullable=False, default=0)
    gst = Column(String(20), nullable=True, default="0")
    freight = Column(Float, nullable=False, default=0)

    @property
    def subtotal(self) -> float:
        return self.quantity * self.unit_price  # type: ignore

    @property
    def gst_rate(self) -> float:
        if self.gst is None or str(self.gst).strip() in ("", "0"):
            return 0.0
        if str(self.gst).startswith("₹"):
            return 0.0
        cleaned = str(self.gst).replace("%", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return 0.0

    @property
    def gst_amount(self) -> float:
        if self.gst and str(self.gst).startswith("₹"):
            cleaned = str(self.gst).replace("₹", "").replace(",", "").strip()
            try:
                return float(cleaned)
            except ValueError:
                return 0.0
        return self.subtotal * self.gst_rate / 100

    @property
    def grand_total(self) -> float:
        return self.subtotal + self.gst_amount + self.freight  # type: ignore

    purchase_order = relationship("PurchaseOrder", back_populates="line_items")


class Sale(Base):
    __tablename__ = "sales"

    id = Column(Integer, primary_key=True, index=True)
    po_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=False)
    po_number = Column(String(100), nullable=False, index=True)
    invoice_number = Column(String(50), nullable=True, unique=True, index=True)
    invoice_date = Column(Date, nullable=True)
    client_name = Column(String(200), nullable=False, index=True)
    project = Column(String(300), nullable=True)
    
    # Financials (Aggregate)
    subtotal = Column(Numeric(12, 2), nullable=False, default=0)
    gst_amount = Column(Numeric(12, 2), nullable=False, default=0)
    freight = Column(Numeric(12, 2), nullable=False, default=0)
    grand_total = Column(Numeric(12, 2), nullable=False, default=0)
    
    payment_status = Column(
        Enum(PaymentStatus), default=PaymentStatus.PENDING, nullable=False
    )
    payment_note = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    created_by = Column(String(100), nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    updated_by = Column(String(100), nullable=True)
    invoice_url = Column(String(500), nullable=True)
    e_way_bill_url = Column(String(500), nullable=True)
    dispatch_from = Column(Text, nullable=True)
    ship_to = Column(Text, nullable=True)
    bill_to = Column(Text, nullable=True)
    dispatched_through = Column(String(200), nullable=True)
    e_way_bill_no = Column(String(100), nullable=True)
    buyers_order_no = Column(String(100), nullable=True)
    payment_terms = Column(String(200), nullable=True)
    # Delivery tracking
    delivery_status = Column(String(50), nullable=False, default="Not Delivered", server_default="Not Delivered")
    delivery_challan_url = Column(String(500), nullable=True)
    hsn_code = Column(String(50), nullable=True)

    # Soft-delete fields
    is_deleted = Column(Boolean, default=False, nullable=False, server_default="0")
    deleted_at = Column(DateTime, nullable=True)
    deleted_by = Column(String(100), nullable=True)
    permanent_delete_at = Column(DateTime, nullable=True)

    @property
    def invoice_urls(self) -> List[str]:
        return [url for url in (self.invoice_url or "").split(";") if url]

    @property
    def e_way_bill_urls(self) -> List[str]:
        return [url for url in (self.e_way_bill_url or "").split(";") if url]

    @property
    def delivery_challan_urls(self) -> List[str]:
        return [url for url in (self.delivery_challan_url or "").split(";") if url]

    @property
    def items_display(self) -> str:
        if self.items:
            return ", ".join(i.item for i in self.items)
        return ""

    purchase_order = relationship("PurchaseOrder", back_populates="sales")
    items = relationship("SaleItem", back_populates="sale", cascade="all, delete-orphan")
    activities = relationship(
        "SaleActivity", back_populates="sale", cascade="all, delete-orphan"
    )
    dispatches = relationship(
        "SaleDispatch", back_populates="sale", cascade="all, delete-orphan", order_by="SaleDispatch.dispatched_at"
    )

class SaleItem(Base):
    __tablename__ = "sale_items"

    id = Column(Integer, primary_key=True, index=True)
    sale_id = Column(Integer, ForeignKey("sales.id"), nullable=False)
    line_item_id = Column(Integer, ForeignKey("po_line_items.id"), nullable=True)
    
    item = Column(String(300), nullable=False)
    uom = Column(String(50), nullable=False, default="Nos")
    quantity = Column(Float, nullable=False, default=0)
    unit_price = Column(Numeric(12, 2), nullable=False, default=0)
    gst_rate = Column(Float, nullable=False, default=0)

    # Pre-calculated totals for this item
    subtotal = Column(Numeric(12, 2), nullable=False, default=0)
    gst_amount = Column(Numeric(12, 2), nullable=False, default=0)
    total_amount = Column(Numeric(12, 2), nullable=False, default=0)

    sale = relationship("Sale", back_populates="items")


class SaleActivity(Base):
    __tablename__ = "sale_activities"

    id = Column(Integer, primary_key=True, index=True)
    sale_id = Column(Integer, ForeignKey("sales.id"), nullable=False)
    action = Column(String(200), nullable=False)
    note = Column(Text, nullable=True)
    payment_status = Column(String(50), nullable=True)
    at = Column(DateTime, server_default=func.now())
    by = Column(String(100), nullable=True)

    sale = relationship("Sale", back_populates="activities")


class SaleDispatch(Base):
    """One row per actual dispatch event against a Sale/invoice — the initial
    dispatch at sale creation, plus one more per subsequent 'Dispatch More'.
    Lets PO fulfillment history show each dispatch separately (with its own
    date/qty/amount) instead of only one row per invoice."""

    __tablename__ = "sale_dispatches"

    id = Column(Integer, primary_key=True, index=True)
    sale_id = Column(Integer, ForeignKey("sales.id"), nullable=False)
    dispatched_at = Column(DateTime, server_default=func.now())
    quantity = Column(Float, nullable=False, default=0)
    uom = Column(String(50), nullable=False, default="Nos")
    subtotal = Column(Numeric(12, 2), nullable=False, default=0)
    gst_amount = Column(Numeric(12, 2), nullable=False, default=0)
    amount = Column(Numeric(12, 2), nullable=False, default=0)
    invoice_number = Column(String(50), nullable=True)
    e_way_bill_no = Column(String(100), nullable=True)
    by = Column(String(100), nullable=True)

    sale = relationship("Sale", back_populates="dispatches")
    items = relationship("SaleDispatchItem", back_populates="dispatch", cascade="all, delete-orphan")


class SaleDispatchItem(Base):
    """Per-item breakdown of one SaleDispatch — so a dispatch covering two
    items with different units (e.g. one in Meter, one in Nos) shows each
    item's own quantity/uom separately instead of one misleading summed
    total."""

    __tablename__ = "sale_dispatch_items"

    id = Column(Integer, primary_key=True, index=True)
    dispatch_id = Column(Integer, ForeignKey("sale_dispatches.id"), nullable=False)
    item = Column(String(300), nullable=False)
    uom = Column(String(50), nullable=False, default="Nos")
    quantity = Column(Float, nullable=False, default=0)
    subtotal = Column(Numeric(12, 2), nullable=False, default=0)
    gst_amount = Column(Numeric(12, 2), nullable=False, default=0)
    amount = Column(Numeric(12, 2), nullable=False, default=0)

    dispatch = relationship("SaleDispatch", back_populates="items")


class Record(Base):
    """Legacy/dashboard sales record (mirrors frontend mock data shape)."""

    __tablename__ = "records"

    id = Column(Integer, primary_key=True, index=True)
    client_name = Column(String(200), nullable=False, index=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=True)
    product = Column(String(300), nullable=False, index=True)
    price = Column(Float, nullable=False, default=0)
    location = Column(String(100), nullable=True)
    payment_status = Column(
        Enum(PaymentStatus), default=PaymentStatus.PENDING, nullable=False
    )
    delivery_status = Column(
        Enum(DeliveryStatus), default=DeliveryStatus.NOT_DELIVERED, nullable=False
    )
    po_number = Column(String(100), nullable=True)
    invoice_number = Column(String(50), nullable=True)
    date = Column(DateTime, server_default=func.now())
    created_at = Column(DateTime, server_default=func.now())

    client_rel = relationship("Client", back_populates="records")


class SystemLog(Base):
    __tablename__ = "system_logs"

    id = Column(Integer, primary_key=True, index=True)
    action = Column(String(200), nullable=False)
    entity_type = Column(String(100), nullable=False)
    entity_id = Column(Integer, nullable=True)
    entity_name = Column(String(300), nullable=True)
    details = Column(Text, nullable=True)
    changed_fields = Column(Text, nullable=True)
    status = Column(String(50), nullable=True, default="Success")
    user = Column(String(100), nullable=True)
    # Which application workspace produced this log (e.g. "Marketing", "Store")
    workspace = Column(String(50), nullable=True, default="Marketing")
    created_at = Column(DateTime, server_default=func.now())


class WorkOrder(Base):
    __tablename__ = "work_orders"

    id = Column(Integer, primary_key=True, index=True)
    client_name = Column(String(200), nullable=False, index=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=True)
    wo_number = Column(String(100), nullable=False, unique=True, index=True)
    wo_date = Column(DateTime, nullable=True)
    item = Column(String(300), nullable=True, default="")
    uom = Column(String(50), nullable=False, default="Nos")
    project = Column(String(300), nullable=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True)
    total_quantity = Column(Float, nullable=False, default=0)
    unit_price = Column(Float, nullable=False, default=0)
    gst = Column(String(20), nullable=True, default="0")
    freight = Column(Float, nullable=False, default=0)
    file_url = Column(String(500), nullable=True)

    work_description = Column(Text, nullable=True)
    site_location = Column(String(200), nullable=True)
    engineer_name = Column(String(150), nullable=True)
    priority = Column(String(20), nullable=False, default="Medium")
    start_date = Column(DateTime, nullable=True)
    target_completion_date = Column(DateTime, nullable=True)
    remarks = Column(Text, nullable=True)

    status = Column(String(50), nullable=False, default="Pending", server_default="Pending")
    closed_at = Column(DateTime, nullable=True)
    closed_by = Column(String(100), nullable=True)
    closed_remark = Column(Text, nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    created_by = Column(String(100), nullable=True)
    last_opened_at = Column(DateTime, nullable=True)
    last_opened_by = Column(String(100), nullable=True)
    last_updated_at = Column(DateTime, nullable=True)
    last_updated_by = Column(String(100), nullable=True)

    # Soft-delete fields
    is_deleted = Column(Boolean, default=False, nullable=False, server_default="0")
    deleted_at = Column(DateTime, nullable=True)
    deleted_by = Column(String(100), nullable=True)
    permanent_delete_at = Column(DateTime, nullable=True)

    client_rel = relationship("Client", back_populates="work_orders")
    project_rel = relationship("Project", back_populates="work_orders")
    line_items = relationship("WOLineItem", back_populates="work_order", cascade="all, delete-orphan", order_by="WOLineItem.id")
    work_order_sales = relationship("WorkOrderSale", back_populates="work_order")

    @property
    def total_qty(self) -> float:
        if self.line_items:
            return sum(li.quantity for li in self.line_items)
        return self.total_quantity  # type: ignore

    @property
    def completed_qty(self) -> float:
        if self.line_items:
            return sum(li.completed_quantity for li in self.line_items)
        return 0.0

    @property
    def pending_quantity(self) -> float:
        return round(max(0, self.total_qty - self.completed_qty), 10)

    @property
    def gst_rate(self) -> float:
        if self.gst is None or str(self.gst).strip() in ("", "0"):
            return 0.0
        if str(self.gst).startswith("₹"):
            return 0.0
        cleaned = str(self.gst).replace("%", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return 0.0

    @property
    def subtotal(self) -> float:
        if self.line_items:
            return sum(li.quantity * li.unit_price for li in self.line_items)
        return self.total_quantity * self.unit_price  # type: ignore

    @property
    def gst_amount(self) -> float:
        if self.gst and str(self.gst).strip() not in ("", "0"):
            if str(self.gst).startswith("₹"):
                cleaned = str(self.gst).replace("₹", "").replace(",", "").strip()
                try:
                    return float(cleaned)
                except ValueError:
                    return 0.0
            else:
                return self.subtotal * self.gst_rate / 100
        if self.line_items:
            return sum(li.gst_amount for li in self.line_items)
        return self.subtotal * self.gst_rate / 100

    @property
    def grand_total(self) -> float:
        items_freight = sum(li.freight for li in self.line_items) if self.line_items else 0.0
        return self.subtotal + self.gst_amount + self.freight + items_freight  # type: ignore

    @property
    def items_display(self) -> str:
        if self.line_items:
            return ", ".join(li.item for li in self.line_items)
        return self.item or ""  # type: ignore


class WOLineItem(Base):
    __tablename__ = "wo_line_items"

    id = Column(Integer, primary_key=True, index=True)
    wo_id = Column(Integer, ForeignKey("work_orders.id"), nullable=False)
    item = Column(String(300), nullable=False)
    quantity = Column(Float, nullable=False, default=0)
    completed_quantity = Column(Float, nullable=False, default=0)
    uom = Column(String(50), nullable=False, default="Nos")
    unit_price = Column(Float, nullable=False, default=0)
    gst = Column(String(20), nullable=True, default="0")
    freight = Column(Float, nullable=False, default=0)

    @property
    def subtotal(self) -> float:
        return self.quantity * self.unit_price  # type: ignore

    @property
    def gst_rate(self) -> float:
        if self.gst is None or str(self.gst).strip() in ("", "0"):
            return 0.0
        if str(self.gst).startswith("₹"):
            return 0.0
        cleaned = str(self.gst).replace("%", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return 0.0

    @property
    def gst_amount(self) -> float:
        if self.gst and str(self.gst).startswith("₹"):
            cleaned = str(self.gst).replace("₹", "").replace(",", "").strip()
            try:
                return float(cleaned)
            except ValueError:
                return 0.0
        return self.subtotal * self.gst_rate / 100

    @property
    def grand_total(self) -> float:
        return self.subtotal + self.gst_amount + self.freight  # type: ignore

    work_order = relationship("WorkOrder", back_populates="line_items")


class WorkOrderSale(Base):
    __tablename__ = "work_order_sales"

    id = Column(Integer, primary_key=True, index=True)
    wo_id = Column(Integer, ForeignKey("work_orders.id"), nullable=False)
    wo_number = Column(String(100), nullable=False, index=True)
    invoice_number = Column(String(50), nullable=True, unique=True, index=True)
    invoice_date = Column(Date, nullable=True)
    client_name = Column(String(200), nullable=False, index=True)
    project = Column(String(300), nullable=True)

    # Financials (Aggregate)
    subtotal = Column(Numeric(12, 2), nullable=False, default=0)
    gst_amount = Column(Numeric(12, 2), nullable=False, default=0)
    freight = Column(Numeric(12, 2), nullable=False, default=0)
    grand_total = Column(Numeric(12, 2), nullable=False, default=0)

    payment_status = Column(
        Enum(PaymentStatus), default=PaymentStatus.PENDING, nullable=False
    )
    payment_note = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    created_by = Column(String(100), nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    updated_by = Column(String(100), nullable=True)
    invoice_url = Column(String(500), nullable=True)
    e_way_bill_url = Column(String(500), nullable=True)
    dispatch_from = Column(Text, nullable=True)
    ship_to = Column(Text, nullable=True)
    bill_to = Column(Text, nullable=True)
    dispatched_through = Column(String(200), nullable=True)
    e_way_bill_no = Column(String(100), nullable=True)
    buyers_order_no = Column(String(100), nullable=True)
    payment_terms = Column(String(200), nullable=True)
    # Delivery tracking
    delivery_status = Column(String(50), nullable=False, default="Not Delivered", server_default="Not Delivered")
    delivery_challan_url = Column(String(500), nullable=True)
    hsn_code = Column(String(50), nullable=True)
    is_deleted = Column(Boolean, default=False, nullable=False, server_default="0")
    deleted_at = Column(DateTime, nullable=True)
    deleted_by = Column(String(100), nullable=True)
    permanent_delete_at = Column(DateTime, nullable=True)

    @property
    def invoice_urls(self) -> List[str]:
        return [url for url in (self.invoice_url or "").split(";") if url]

    @property
    def e_way_bill_urls(self) -> List[str]:
        return [url for url in (self.e_way_bill_url or "").split(";") if url]

    @property
    def delivery_challan_urls(self) -> List[str]:
        return [url for url in (self.delivery_challan_url or "").split(";") if url]

    @property
    def items_display(self) -> str:
        if self.items:
            return ", ".join(i.item for i in self.items)
        return ""

    work_order = relationship("WorkOrder", back_populates="work_order_sales")
    items = relationship("WorkOrderSaleItem", back_populates="sale", cascade="all, delete-orphan")
    activities = relationship(
        "WorkOrderSaleActivity", back_populates="sale", cascade="all, delete-orphan"
    )
    dispatches = relationship(
        "WorkOrderSaleDispatch", back_populates="sale", cascade="all, delete-orphan", order_by="WorkOrderSaleDispatch.dispatched_at"
    )


class WorkOrderSaleItem(Base):
    __tablename__ = "work_order_sale_items"

    id = Column(Integer, primary_key=True, index=True)
    sale_id = Column(Integer, ForeignKey("work_order_sales.id"), nullable=False)
    line_item_id = Column(Integer, ForeignKey("wo_line_items.id"), nullable=True)

    item = Column(String(300), nullable=False)
    uom = Column(String(50), nullable=False, default="Nos")
    quantity = Column(Float, nullable=False, default=0)
    unit_price = Column(Numeric(12, 2), nullable=False, default=0)
    gst_rate = Column(Float, nullable=False, default=0)

    # Pre-calculated totals for this item
    subtotal = Column(Numeric(12, 2), nullable=False, default=0)
    gst_amount = Column(Numeric(12, 2), nullable=False, default=0)
    total_amount = Column(Numeric(12, 2), nullable=False, default=0)

    sale = relationship("WorkOrderSale", back_populates="items")


class WorkOrderSaleActivity(Base):
    __tablename__ = "work_order_sale_activities"

    id = Column(Integer, primary_key=True, index=True)
    sale_id = Column(Integer, ForeignKey("work_order_sales.id"), nullable=False)
    action = Column(String(200), nullable=False)
    note = Column(Text, nullable=True)
    payment_status = Column(String(50), nullable=True)
    at = Column(DateTime, server_default=func.now())
    by = Column(String(100), nullable=True)

    sale = relationship("WorkOrderSale", back_populates="activities")


class WorkOrderSaleDispatch(Base):
    """One row per actual dispatch event against a WorkOrderSale/invoice — the
    initial dispatch at sale creation, plus one more per subsequent 'Dispatch
    More'. Mirrors SaleDispatch for Work Orders."""

    __tablename__ = "work_order_sale_dispatches"

    id = Column(Integer, primary_key=True, index=True)
    sale_id = Column(Integer, ForeignKey("work_order_sales.id"), nullable=False)
    dispatched_at = Column(DateTime, server_default=func.now())
    quantity = Column(Float, nullable=False, default=0)
    uom = Column(String(50), nullable=False, default="Nos")
    subtotal = Column(Numeric(12, 2), nullable=False, default=0)
    gst_amount = Column(Numeric(12, 2), nullable=False, default=0)
    amount = Column(Numeric(12, 2), nullable=False, default=0)
    invoice_number = Column(String(50), nullable=True)
    e_way_bill_no = Column(String(100), nullable=True)
    by = Column(String(100), nullable=True)

    sale = relationship("WorkOrderSale", back_populates="dispatches")
    items = relationship("WorkOrderSaleDispatchItem", back_populates="dispatch", cascade="all, delete-orphan")


class WorkOrderSaleDispatchItem(Base):
    """Per-item breakdown of one WorkOrderSaleDispatch — mirrors
    SaleDispatchItem for Work Orders."""

    __tablename__ = "work_order_sale_dispatch_items"

    id = Column(Integer, primary_key=True, index=True)
    dispatch_id = Column(Integer, ForeignKey("work_order_sale_dispatches.id"), nullable=False)
    item = Column(String(300), nullable=False)
    uom = Column(String(50), nullable=False, default="Nos")
    quantity = Column(Float, nullable=False, default=0)
    subtotal = Column(Numeric(12, 2), nullable=False, default=0)
    gst_amount = Column(Numeric(12, 2), nullable=False, default=0)
    amount = Column(Numeric(12, 2), nullable=False, default=0)

    dispatch = relationship("WorkOrderSaleDispatch", back_populates="items")


class UserSession(Base):
    __tablename__ = "user_sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    user_name = Column(String(100), nullable=False)
    user_email = Column(String(150), nullable=False)
    login_at = Column(DateTime, server_default=func.now())
    logout_at = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)

    user_rel = relationship("User", foreign_keys=[user_id])


class CompanyAddress(Base):
    __tablename__ = "company_addresses"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    address_text = Column(Text, nullable=False)
    is_default = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class CreditNote(Base):
    """Adjustment document against a PO Sale or WO Sale.
    Never modifies the original sale — acts as a separate linked record.
    sale_type = 'PO' → sale_id references sales.id
    sale_type = 'WO' → wo_sale_id references work_order_sales.id
    """
    __tablename__ = "credit_notes"

    id = Column(Integer, primary_key=True, index=True)
    cn_number = Column(String(50), nullable=False, index=True)
    cn_date = Column(Date, nullable=False)
    sale_type = Column(String(5), nullable=False)  # 'PO' or 'WO'

    # FK to the original sale (one of these will be non-null)
    sale_id = Column(Integer, ForeignKey("sales.id"), nullable=True)
    wo_sale_id = Column(Integer, ForeignKey("work_order_sales.id"), nullable=True)

    # Denormalised for fast listing (copied from original sale at creation)
    invoice_number = Column(String(50), nullable=True)
    invoice_date = Column(Date, nullable=True)
    po_number = Column(String(100), nullable=True)
    client_name = Column(String(200), nullable=False)
    project = Column(String(300), nullable=True)

    reason = Column(String(100), nullable=False)   # one of the dropdown values

    # Computed totals (stored at creation, same as WorkOrderSale pattern)
    taxable_amount = Column(Numeric(12, 2), nullable=False, default=0)
    gst_amount = Column(Numeric(12, 2), nullable=False, default=0)
    total_amount = Column(Numeric(12, 2), nullable=False, default=0)

    status = Column(String(20), nullable=False, default="Issued", server_default="Issued")

    created_by = Column(String(100), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_by = Column(String(100), nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    # Soft-delete
    is_deleted = Column(Boolean, default=False, nullable=False, server_default="0")
    deleted_at = Column(DateTime, nullable=True)
    deleted_by = Column(String(100), nullable=True)
    permanent_delete_at = Column(DateTime, nullable=True)

    # Relationships
    sale = relationship("Sale", foreign_keys=[sale_id], backref="credit_notes")
    wo_sale = relationship("WorkOrderSale", foreign_keys=[wo_sale_id], backref="credit_notes")
    items = relationship("CreditNoteItem", back_populates="credit_note", cascade="all, delete-orphan")


class CreditNoteItem(Base):
    __tablename__ = "credit_note_items"

    id = Column(Integer, primary_key=True, index=True)
    credit_note_id = Column(Integer, ForeignKey("credit_notes.id"), nullable=False)

    item = Column(String(300), nullable=False)
    uom = Column(String(50), nullable=False, default="Nos")
    original_qty = Column(Float, nullable=False, default=0)
    credit_qty = Column(Float, nullable=False, default=0)
    unit_price = Column(Numeric(12, 2), nullable=False, default=0)
    gst_rate = Column(Float, nullable=False, default=0)

    # Stored totals (credit_qty × unit_price)
    subtotal = Column(Numeric(12, 2), nullable=False, default=0)
    gst_amount = Column(Numeric(12, 2), nullable=False, default=0)
    total_amount = Column(Numeric(12, 2), nullable=False, default=0)

    credit_note = relationship("CreditNote", back_populates="items")


class StoreVendor(Base):
    __tablename__ = "store_vendors"

    id = Column(Integer, primary_key=True, index=True)
    vendor_name = Column(String(255), nullable=False, index=True)
    person_name = Column(String(255), nullable=True)
    vendor_gst = Column(String(50), nullable=True)
    contact = Column(String(100), nullable=True)
    address = Column(Text, nullable=True)
    items_supplied = Column(Text, nullable=True)
    status = Column(String(50), nullable=False, default="Active")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
