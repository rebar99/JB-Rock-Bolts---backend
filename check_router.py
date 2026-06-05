try:
    from app.routers.dashboard import router
    print("Dashboard router imported successfully")
except Exception as e:
    print(f"Error importing dashboard router: {e}")
    import traceback
    traceback.print_exc()
