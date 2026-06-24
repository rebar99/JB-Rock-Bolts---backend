import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app.routers.reports import get_pending_pos_report
from app.database import SessionLocal

if __name__ == '__main__':
    db = SessionLocal()
    try:
        out = get_pending_pos_report(db=db)
        print('OK')
        import json
        print(json.dumps(out.dict(), indent=2))
    except Exception as e:
        import traceback
        traceback.print_exc()
    finally:
        db.close()
