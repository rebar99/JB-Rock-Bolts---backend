from app.models.models import (  # noqa: F401
    Base,
    User,
    Client,
    Product,
    PurchaseOrder,
    Sale,
    SaleActivity,
    Record,
)
# HR models registered separately — imported in main.py via `from app.models import models` (noqa)
