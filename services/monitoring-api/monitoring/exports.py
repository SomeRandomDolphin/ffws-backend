import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import xlsxwriter
from sqlalchemy import select, update, delete

from monitoring.catalog import get_station
from monitoring.queries import latest_time
from monitoring.store import jobs, observations as obs, now, WIB

EXPORT_DIR = Path(os.getenv("EXPORT_DIR", "/app/exports"))

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
    headers = ["Waktu pengamatan (WIB)", "Sensor", "Parameter", "Nilai asli", "Satuan", "Sumber", "Verifikasi", "ID sumber", "Diterima (WIB)"]
    try:
        for offset in range(12):
            month = shift_month(first_month, offset)
            end_month = shift_month(month, 1)
            start_utc = month.replace(tzinfo=WIB).astimezone(utc).replace(tzinfo=None)
            end_utc = end_month.replace(tzinfo=WIB).astimezone(utc).replace(tzinfo=None)
            part = 1
            def new_sheet():
                suffix = "" if part == 1 else f" ({part})"
                sheet = workbook.add_worksheet(month.strftime("%Y-%m") + suffix)
                sheet.write(0, 0, station["name"], title)
                sheet.write(1, 0, "Catatan asli; sumber API dan Excel dipertahankan. Data kosong bukan nilai nol.")
                sheet.write(2, 0, "Periode: " + month.strftime("%Y-%m") + "; zona waktu Asia/Jakarta; sampai pengamatan terakhir yang tersedia.")
                sheet.write_row(4, 0, headers, heading)
                sheet.freeze_panes(5, 1)
                sheet.set_column(0, 0, 24)
                sheet.set_column(1, 2, 22)
                sheet.set_column(3, 4, 14)
                sheet.set_column(5, 8, 30)
                return sheet
            sheet, row = new_sheet(), 5
            with db.connect().execution_options(stream_results=True) as conn:
                results = conn.execute(select(obs).where(obs.c.station == job["station"],
                    obs.c.observed_at >= start_utc, obs.c.observed_at < end_utc,
                    obs.c.observed_at <= latest).order_by(obs.c.observed_at, obs.c.id)).mappings()
                for item in results:
                    if row >= 1048576:
                        sheet.autofilter(4, 0, row - 1, len(headers) - 1)
                        part += 1
                        sheet, row = new_sheet(), 5
                    local = item["observed_at"].replace(tzinfo=utc).astimezone(WIB).replace(tzinfo=None)
                    received = item["received_at"].replace(tzinfo=utc).astimezone(WIB).replace(tzinfo=None)
                    sheet.write_datetime(row, 0, local, date_format)
                    sheet.write_row(row, 1, [item["sensor"], item["metric"]])
                    if item["value"] is not None:
                        sheet.write_number(row, 3, item["value"], number_format)
                    sheet.write_row(row, 4, [item["unit"], item["source"], item["verification"], item["record_key"]])
                    sheet.write_datetime(row, 8, received, date_format)
                    row += 1
            if row == 5:
                sheet.write(5, 0, "Tidak ada data pada bulan ini.")
            sheet.autofilter(4, 0, max(4, row - 1), len(headers) - 1)
    finally:
        workbook.close()
    with db.begin() as conn:
        conn.execute(update(jobs).where(jobs.c.id == job["id"]).values(status="ready",
            filename=f"{job['station']}_{first_month:%Y-%m}_{last_month:%Y-%m}.xlsx"))

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
