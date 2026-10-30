import argparse
import logging
import os
import signal
import threading
import time
from pathlib import Path

from sqlalchemy import update
from monitoring.collect import import_excel, sync_surabaya, sync_welang_api
from monitoring.exports import process_jobs, refresh_export_caches
from monitoring import shadow
from monitoring.store import engine, initialize, jobs, set_source, cloud_mode, now

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
        path = args.path or os.getenv("WELANG_IMPORT_PATH") or os.getenv("WELANG_EXCEL_PATH", "/app/imports/history")
        targets = sorted(p for p in Path(path).iterdir() if p.suffix.lower() in (".csv", ".xlsx", ".xlsm")) if Path(path).is_dir() else [Path(path)]
        for target in targets:
            print(target.name, import_excel(db, target))
        return
    lease=None
    if cloud_mode():
        from sqlalchemy import text
        lease=db.connect().execution_options(isolation_level="AUTOCOMMIT")
        if not lease.execute(text("SELECT pg_try_advisory_lock(8675309123)")).scalar():
            lease.close()
            raise RuntimeError("Another central worker is already running")
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    # Compose runs one worker instance; recover an interrupted export on restart.
    with db.begin() as conn:
        conn.execute(update(jobs).where(jobs.c.status == "processing").values(status="queued"))
    try:
        if not cloud_mode(): refresh_export_caches(db)
    except Exception:
        log.error("Ekspor siap unduh belum dapat dijadwalkan.")

    def collect():
        while not stop.is_set():
            started = time.monotonic()
            try:
                if lease is not None: lease.exec_driver_sql("SELECT 1")
                configured_path = Path(os.getenv("WELANG_IMPORT_PATH") or os.getenv("WELANG_EXCEL_PATH", "/app/imports/history"))
                paths = sorted(p for p in configured_path.iterdir() if p.suffix.lower() in (".csv", ".xlsx", ".xlsm")) if configured_path.is_dir() else [configured_path]
                for path in paths:
                    if not path.is_file():
                        continue
                    try:
                        import_excel(db, path)
                    except Exception:
                        set_source(db, "excel:welang", error="Impor arsip Welang gagal; periksa format file dan laporan impor.")
                if cloud_mode():
                    from monitoring.cloud import sync
                    sync(db)
                    set_source(db,"worker:central",last_success=now(),error=None)
                else: sync_surabaya(db)
                sync_welang_api(db)
                if not cloud_mode(): refresh_export_caches(db)
            except Exception:
                if lease is not None and (lease.closed or lease.invalidated): stop.set()
                log.error("Siklus pengumpulan gagal; akan dicoba ulang tanpa menghapus arsip.")
            stop.wait(max(1, 30 - (time.monotonic() - started)))
    collector = threading.Thread(target=collect, daemon=True)
    collector.start()
    def predict_shadow():
        while not stop.is_set():
            try:
                shadow.cycle(db)
            except Exception:
                log.exception("Shadow forecast cycle failed; observation collection continues.")
            stop.wait(60)
    shadow_thread = None
    if shadow.enabled():
        shadow_thread = threading.Thread(target=predict_shadow, daemon=True)
        shadow_thread.start()
    while not stop.is_set():
        try:
            if not cloud_mode(): process_jobs(db)
        except Exception:
            log.error("Antrean ekspor belum dapat diproses; mencoba ulang.")
        stop.wait(2)
    collector.join(timeout=30)
    if shadow_thread: shadow_thread.join(timeout=30)
    if lease is not None: lease.close()

if __name__ == "__main__":
    main()
