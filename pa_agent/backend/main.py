"""
ClipButler service entry point.
Starts the ingest scanner in a background thread and the FastAPI server.

Usage:
    python backend/main.py [--port 8765] [--config path/to/config.json]
"""

import argparse
import logging
import socket
import threading
import time
import webbrowser

import uvicorn

from .config import ConfigManager
from .db.sqlite_db import SQLiteDB
from .db.vector_db import VectorDB
from .core.scanner import IngestScanner
from .api.server import create_app
from .security.license import LicenseManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("clipbutler")


def print_banner():
    print(r"""
  ____  _ _         ____        _   _
 / ___|| (_)_ __   | __ ) _   _| |_| | ___ _ __
| |    | | | '_ \  |  _ \| | | | __| |/ _ \ '__|
| |___ | | | |_) | | |_) | |_| | |_| |  __/ |
 \____||_|_| .__/  |____/ \__,_|\__|_|\___|_|
           |_|
 Video Intelligence System  |  localhost:8765
""")


def main():
    parser = argparse.ArgumentParser(description="ClipButler Service")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--config", default=None, help="Path to config.json")
    parser.add_argument("--setup", action="store_true", help="Run setup wizard")
    args = parser.parse_args()

    print_banner()

    # ---- Config ----
    config = ConfigManager(args.config)

    # ---- Setup wizard ----
    if args.setup:
        _run_setup_wizard(config)

    # ---- Databases ----
    sqlite_db = SQLiteDB(config.db_path)
    vector_db = VectorDB(config.chroma_path)
    logger.info(f"Databases: {config.db_path}")

    # Recover any files stuck in PROCESSING from a previous crash
    recovered = sqlite_db.recover_stuck_processing()
    if recovered:
        logger.info(f"Recovered {recovered} file(s) stuck in PROCESSING → PENDING")

    # ---- License ----
    license_mgr = LicenseManager(config)
    license_key = config.get("license_key", "")
    if license_key:
        status, msg = license_mgr.validate(license_key)
        logger.info(f"License: {status} — {msg}")
    else:
        logger.warning(
            "No license key configured. AI analysis disabled until a license key is added.\n"
            "Add your license key at http://localhost:8765/ui (Settings tab)."
        )
        license_mgr._ingest_allowed = True  # Allow ingest in trial/dev

    # ---- Watch paths ----
    watch_paths = config.get("watch_paths", [])
    if not watch_paths:
        logger.warning("No watch paths configured. Add them at http://localhost:8765/ui")

    # ---- Scanner ----
    scanner = IngestScanner(
        config_manager=config,
        proxy_folder=config.proxy_folder,
        thumbnail_folder=config.thumbnail_folder,
        sqlite_db=sqlite_db,
        vector_db=vector_db,
    )

    scanner_thread = threading.Thread(
        target=scanner.run_loop,
        kwargs={"interval_sec": 10.0},
        daemon=True,
        name="ingest-scanner",
    )
    scanner_thread.start()
    logger.info("Ingest scanner started")

    # ---- Check for existing instance ----
    def _port_in_use(host, port):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex((host, port)) == 0

    if _port_in_use(args.host, args.port):
        logger.info(f"CLPBTLR is already running on port {args.port}. Opening browser.")
        webbrowser.open(f"http://localhost:{args.port}/ui")
        return

    # ---- Auto-open browser ----
    def _open_browser(port):
        time.sleep(2)  # wait for uvicorn to start
        webbrowser.open(f"http://localhost:{port}/ui")

    threading.Thread(target=_open_browser, args=(args.port,), daemon=True).start()

    # ---- FastAPI ----
    app = create_app(
        sqlite_db=sqlite_db,
        vector_db=vector_db,
        scanner=scanner,
        config_manager=config,
        license_manager=license_mgr,
    )

    logger.info(f"Starting API server at http://{args.host}:{args.port}")
    logger.info(f"Control UI: http://localhost:{args.port}/ui")
    logger.info(f"API docs:   http://localhost:{args.port}/api/docs")

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


def _run_setup_wizard(config: ConfigManager):
    print("\n=== ClipButler Setup ===\n")

    # Watch paths
    current_paths = config.get("watch_paths", [])
    print(f"Current watch paths: {current_paths or 'none'}")
    print("Enter folder paths to monitor (one per line, blank line to finish):")
    paths = []
    while True:
        p = input("  Path: ").strip()
        if not p:
            break
        paths.append(p)
    if paths:
        config.update({"watch_paths": paths})
    elif not current_paths:
        config.update({"watch_paths": []})

    # License key
    print("\nEnter license key (or press Enter to skip):")
    lk = input("  License key: ").strip()
    if lk:
        config.update({"license_key": lk})

    # Proxy URL override (for developers/self-hosters)
    current_proxy = config.get("proxy_url", "https://clipbutler-production.up.railway.app")
    print(f"\nProxy service URL [{current_proxy}] (press Enter to keep):")
    pu = input("  Proxy URL: ").strip()
    if pu:
        config.update({"proxy_url": pu})

    print("\nSetup complete. Starting service...\n")


if __name__ == "__main__":
    main()
