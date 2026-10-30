import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import xlsxwriter
from sqlalchemy import select, update, delete, func

from monitoring.catalog import get_station
from monitoring.queries import latest_time
from monitoring.store import jobs, export_cache, observations as obs, now, WIB

EXPORT_DIR = Path(os.getenv("EXPORT_DIR", "/app/exports"))
CACHE_TTL = timedelta(days=3650)

def archive_fingerprint(db, station):
    with db.connect() as conn:
        latest_id = conn.execute(select(func.max(obs.c.id)).where(obs.c.station == station)).scalar()
    return str(latest_id or 0)

def _queue_cache(conn, station, fingerprint):
    cached = conn.execute(select(export_cache).where(export_cache.c.station == station)).mappings().first()
    if cached and cached["pending_job_id"]:
        pending = conn.execute(select(jobs.c.status).where(jobs.c.id == cached["pending_job_id"])).scalar()
        if pending in ("queued", "processing"):
            return
    active = conn.execute(select(jobs.c.id).where(jobs.c.status.in_(["queued", "processing"])).limit(1)).scalar()
    if active:
        return
    job_id = __import__("uuid").uuid4().hex
    conn.execute(jobs.insert().values(id=job_id, station=station, status="queued", created_at=now(), expires_at=now() + CACHE_TTL))
    values = {"station": station, "fingerprint": fingerprint, "pending_job_id": job_id, "updated_at": now()}
    if cached:
        conn.execute(update(export_cache).where(export_cache.c.station == station).values(**values))
    else:
        conn.execute(export_cache.insert().values(**values, job_id=None))

def refresh_export_caches(db):
    """Queue at most one stale station archive, keeping work out of download requests."""
    with db.connect() as conn:
        station_ids = conn.execute(select(obs.c.station).distinct()).scalars().all()
    for station in station_ids:
        fingerprint = archive_fingerprint(db, station)
        with db.begin() as conn:
            cached = conn.execute(select(export_cache).where(export_cache.c.station == station)).mappings().first()
            if not cached:
                previous = conn.execute(select(jobs).where(jobs.c.station == station,
                    jobs.c.status == "ready", jobs.c.expires_at > now()).order_by(jobs.c.created_at.desc()).limit(1)).mappings().first()
                if previous and (EXPORT_DIR / (previous["id"] + ".xlsx")).is_file():
                    conn.execute(export_cache.insert().values(station=station, fingerprint="legacy",
                        job_id=previous["id"], pending_job_id=None, updated_at=now()))
                    cached = conn.execute(select(export_cache).where(export_cache.c.station == station)).mappings().first()
            if cached and cached["fingerprint"] == fingerprint and cached["job_id"]:
                continue
            _queue_cache(conn, station, fingerprint)
            pending = conn.execute(select(export_cache.c.pending_job_id).where(export_cache.c.station == station)).scalar()
            if pending:
                return

def cached_export_or_queue(db, station):
    fingerprint = archive_fingerprint(db, station)
    with db.begin() as conn:
        cached = conn.execute(select(export_cache).where(export_cache.c.station == station)).mappings().first()
        if not cached:
            previous = conn.execute(select(jobs).where(jobs.c.station == station,
                jobs.c.status == "ready", jobs.c.expires_at > now()).order_by(jobs.c.created_at.desc()).limit(1)).mappings().first()
            if previous and (EXPORT_DIR / (previous["id"] + ".xlsx")).is_file():
                conn.execute(export_cache.insert().values(station=station, fingerprint="legacy",
                    job_id=previous["id"], pending_job_id=None, updated_at=now()))
                cached = conn.execute(select(export_cache).where(export_cache.c.station == station)).mappings().first()
        if not cached or cached["fingerprint"] != fingerprint or not cached["job_id"]:
            _queue_cache(conn, station, fingerprint)
            cached = conn.execute(select(export_cache).where(export_cache.c.station == station)).mappings().first()
        if cached and cached["job_id"]:
            ready = conn.execute(select(jobs.c.status).where(jobs.c.id == cached["job_id"])).scalar()
            if ready == "ready":
                return {"id": cached["job_id"], "status": "ready"}
        if cached and cached["pending_job_id"]:
            status = conn.execute(select(jobs.c.status).where(jobs.c.id == cached["pending_job_id"])).scalar()
            if status in ("queued", "processing"):
                return {"id": cached["pending_job_id"], "status": status}
    return None

def month_start(value):
    return value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

def shift_month(value, delta):
    month = value.year * 12 + value.month - 1 + delta
    return value.replace(year=month // 12, month=month % 12 + 1, day=1)

def run_export(db, job):
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = EXPORT_DIR / (job["id"] + ".xlsx")
    latest = latest_time(db, job["station"])
    if latest is None:
        raise ValueError("Data stasiun belum tersedia")
    utc = timezone.utc
    last_month = month_start(latest.replace(tzinfo=utc).astimezone(WIB).replace(tzinfo=None))
    first_month = shift_month(last_month, -11)
    station = get_station(job["station"])
    workbook = xlsxwriter.Workbook(str(path), {"constant_memory": True, "strings_to_formulas": False, "strings_to_urls": False})
    title = workbook.add_format({"bold": True})
    heading = workbook.add_format({"bold": True, "bg_color": "#17324D", "font_color": "white"})
    date_format = workbook.add_format({"num_format": "yyyy-mm-dd hh:mm:ss"})
    number_format = workbook.add_format({"num_format": "0.000"})
    headers = ["Waktu pengamatan (WIB)", "Sensor", "Parameter", "Nilai", "Satuan", "Diterima (WIB)"]
    months = [shift_month(first_month, offset) for offset in range(12)]

    def new_sheet(month, part):
        suffix = "" if part == 1 else f" ({part})"
        sheet = workbook.add_worksheet(month.strftime("%Y-%m") + suffix)
        sheet.write(0, 0, station["name"], title)
        sheet.write(1, 0, "Nilai dan satuan mengikuti data sumber. Data kosong bukan nilai nol.")
        sheet.write(2, 0, "Periode: " + month.strftime("%Y-%m") + "; zona waktu Asia/Jakarta; sampai pengamatan terakhir yang tersedia.")
        sheet.write_row(4, 0, headers, heading)
        sheet.freeze_panes(5, 1)
        sheet.set_column(0, 0, 24)
        sheet.set_column(1, 2, 22)
        sheet.set_column(3, 4, 14)
        sheet.set_column(5, 5, 24)
        return sheet

    states = [{"month": month, "part": 1, "sheet": new_sheet(month, 1), "row": 5} for month in months]
    start_utc = first_month.replace(tzinfo=WIB).astimezone(utc).replace(tzinfo=None)
    try:
        with db.connect().execution_options(stream_results=True) as conn:
            results = conn.execute(
                select(obs.c.sensor, obs.c.metric, obs.c.observed_at, obs.c.received_at, obs.c.value, obs.c.unit)
                .where(obs.c.station == job["station"], obs.c.observed_at >= start_utc, obs.c.observed_at <= latest)
                .order_by(obs.c.observed_at, obs.c.id)
            ).mappings()
            for item in results:
                local = item["observed_at"].replace(tzinfo=utc).astimezone(WIB).replace(tzinfo=None)
                month_index = (local.year - first_month.year) * 12 + local.month - first_month.month
                if not 0 <= month_index < len(states):
                    continue
                state = states[month_index]
                if state["row"] >= 1048576:
                    state["sheet"].autofilter(4, 0, state["row"] - 1, len(headers) - 1)
                    state["part"] += 1
                    state["sheet"] = new_sheet(state["month"], state["part"])
                    state["row"] = 5
                row = state["row"]
                received = item["received_at"].replace(tzinfo=utc).astimezone(WIB).replace(tzinfo=None)
                state["sheet"].write_datetime(row, 0, local, date_format)
                state["sheet"].write_row(row, 1, [item["sensor"], item["metric"]])
                if item["value"] is not None:
                    state["sheet"].write_number(row, 3, item["value"], number_format)
                state["sheet"].write(row, 4, "mm" if item["metric"] == "rainfall" and item["sensor"] != "jumlah_tip" else item["unit"])
                state["sheet"].write_datetime(row, 5, received, date_format)
                state["row"] += 1
        for state in states:
            if state["row"] == 5:
                state["sheet"].write(5, 0, "Tidak ada data pada bulan ini.")
            state["sheet"].autofilter(4, 0, max(4, state["row"] - 1), len(headers) - 1)
    finally:
        workbook.close()
    with db.begin() as conn:
        conn.execute(update(jobs).where(jobs.c.id == job["id"]).values(status="ready",
            filename=f"{job['station']}_{first_month:%Y-%m}_{last_month:%Y-%m}.xlsx"))
        cached = conn.execute(select(export_cache).where(export_cache.c.pending_job_id == job["id"])).mappings().first()
        old_job_id = cached["job_id"] if cached else None
        if cached:
            conn.execute(update(export_cache).where(export_cache.c.station == job["station"]).values(
                job_id=job["id"], pending_job_id=None, updated_at=now()))
            if old_job_id and old_job_id != job["id"]:
                conn.execute(delete(jobs).where(jobs.c.id == old_job_id))
    if old_job_id and old_job_id != job["id"]:
        (EXPORT_DIR / (old_job_id + ".xlsx")).unlink(missing_ok=True)
def process_jobs(db):
    with db.begin() as conn:
        expired = conn.execute(select(jobs.c.id).where(jobs.c.expires_at < now())).scalars().all()
        for job_id in expired:
            (EXPORT_DIR / (job_id + ".xlsx")).unlink(missing_ok=True)
        conn.execute(delete(jobs).where(jobs.c.expires_at < now()))
        job = conn.execute(select(jobs).where(jobs.c.status == "queued").order_by(jobs.c.created_at).limit(1)).mappings().first()
        if not job:
            return
        conn.execute(update(jobs).where(jobs.c.id == job["id"]).values(status="processing"))
    try:
        run_export(db, job)
    except Exception:
        (EXPORT_DIR / (job["id"] + ".xlsx")).unlink(missing_ok=True)
        with db.begin() as conn:
            conn.execute(update(jobs).where(jobs.c.id == job["id"]).values(status="failed", error="Pembuatan Excel gagal; coba ulangi."))
