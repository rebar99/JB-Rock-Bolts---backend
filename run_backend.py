from __future__ import annotations
import argparse
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR))
os.chdir(ROOT_DIR)

import uvicorn


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the JB Rock Bolts backend API")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload")
    parser.add_argument("--no-reload", dest="reload", action="store_false", help="Disable auto-reload")
    parser.add_argument("--log-level", default="info", help="Uvicorn log level")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
    )
