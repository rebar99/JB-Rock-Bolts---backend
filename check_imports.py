import sys, os
sys.path.append('d:/jbrocks1/JB-Rock-Bolts---backend')
modules = [
    "app.models.models",
    "app.routers.purchase_orders",
    "app.schemas.purchase_order",
    "app.main"
]
for m in modules:
    try:
        __import__(m)
        print(f"{m} imported successfully")
    except Exception as e:
        print(f"Error importing {m}: {e}")
