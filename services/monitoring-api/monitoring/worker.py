import argparse
import logging
import os
import signal
import threading
import time
from pathlib import Path

from sqlalchemy import update
from monitoring.collect import import_excel, sync_surabaya, sync_welang_api
from monitoring.exports import process_jobs
from monitoring.store import engine, initialize, jobs, set_source

log = logging.getLogger("monitoring.worker")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["run", "init", "import-excel"], nargs="?", default="run")
    parser.add_argument("--path")
    args = parser.parse_args()
    db = engine()
    initialize(db)
    if args.command == "init":
        return
    if args.command == "import-excel":
        print(import_excel(db, args.path or os.environ["WELANG_EXCEL_PATH"]))
        return
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    # Compose runs one worker instance; recover an interrupted export on restart.
    with db.begin() as conn:
        conn.execute(update(jobs).where(jobs.c.status == "processing").values(status="queued"))

    def collect():
        while not stop.is_set():
            started = time.monotonic()
            try:
                path = Path(os.getenv("WELANG_EXCEL_PATH", "/app/imports/welang.xlsx"))
                if path.is_file():
                    try:
                        import_excel(db, path)
                    except Exception:
                        set_source(db, "excel:welang", error="Impor Excel gagal; periksa format file dan laporan impor.")
                sync_surabaya(db)
                sync_welang_api(db)
            except Exception:
                log.error("Siklus pengumpulan gagal; akan dicoba ulang tanpa menghapus arsip.")
            stop.wait(max(1, 30 - (time.monotonic() - started)))
    collector = threading.Thread(target=collect, daemon=True)
    collector.start()
    while not stop.is_set():
        try:
            process_jobs(db)
        except Exception:
            log.error("Antrean ekspor belum dapat diproses; mencoba ulang.")
        stop.wait(2)
    collector.join(timeout=30)

if __name__ == "__main__":
    main()
