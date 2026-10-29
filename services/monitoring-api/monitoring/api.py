import os
from datetime import timedelta, datetime
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select, func, text

from monitoring.catalog import stations, get_station, live_config, measurement
from monitoring.queries import snapshot, history, latest_time
from monitoring.store import engine, initialize, iso, timestamp, now, jobs
from monitoring.exports import EXPORT_DIR, cached_export_or_queue

router = APIRouter()
_db = None

def database():
    global _db
    if _db is None:
        _db = engine()
    return _db

def require_station(station):
    if get_station(station) is None:
        raise HTTPException(404, "Stasiun tidak dikenal")

@router.get("/health/live")
def live():
    return {"status": "ok"}

@router.get("/health/ready")
@router.get("/health")
def ready():
    try:
        with database().connect() as conn:
            conn.execute(text("SELECT 1 FROM observations LIMIT 1"))
        return {"status": "ok", "ready": True, "archiveReady": True}
    except Exception:
        raise HTTPException(503, "Arsip belum siap")

@router.get("/v1/capabilities")
def capabilities():
    return {"laravel": {"enabled": True, "features": [
        {"name": name, "available": True, "usedByNewFrontend": False}
        for name in ("auth", "roles", "contacts", "station_configuration", "excel_import", "history")
    ]}, "predictions": {"enabled": os.getenv("PREDICTIONS_ENABLED", "false").lower() == "true"},
        "telegram": {"enabled": os.getenv("TELEGRAM_ENABLED", "false").lower() == "true"},
        "welangApi": {"configured": bool(os.getenv("WELANG_API_URL"))}}

@router.get("/v1/stations")
def station_list(region: str | None = None):
    return {"stations": [snapshot(database(), s["id"]) for s in stations()
                         if s["kind"] == "water_level" and (region is None or s["region"] == region)]}

@router.get("/v1/stations/{station}/snapshot")
def station_snapshot(station: str):
    require_station(station)
    return snapshot(database(), station)

@router.get("/v1/stations/{station}/history")
def station_history(station: str, hours: int = Query(24, ge=1, le=744),
                    start: datetime | None = Query(None, alias="from"),
                    end: datetime | None = Query(None, alias="to"),
                    sensor: str | None = Query(None, min_length=1, max_length=100)):
    require_station(station)
    until = timestamp(end) if end else latest_time(database(), station, sensor=sensor)
    if until is None:
        return {"station": station, "from": None, "to": None, "series": []}
    since = timestamp(start) if start else until - timedelta(hours=hours)
    if since > until or until - since > timedelta(days=31):
        raise HTTPException(422, "Rentang grafik harus 0 hingga 31 hari; gunakan ekspor untuk riwayat tahunan")
    return history(database(), station, since, until, sensor=sensor)

@router.post("/v1/stations/{station}/exports", status_code=202)
def create_export(station: str):
    require_station(station)
    db = database()
    if latest_time(db, station) is None:
        raise HTTPException(409, "Belum ada data untuk diunduh")
    cached = cached_export_or_queue(db, station)
    if cached:
        return cached
    with db.begin() as conn:
        pending = conn.execute(select(jobs).where(jobs.c.station == station,
            jobs.c.status.in_(["queued", "processing"]), jobs.c.expires_at > now())).mappings().first()
        if pending:
            return {"id": pending["id"], "status": pending["status"]}
        count = conn.execute(select(func.count()).select_from(jobs).where(jobs.c.status.in_(["queued", "processing"]))).scalar()
        if count >= 10:
            raise HTTPException(429, "Antrean ekspor penuh; coba lagi nanti")
        job_id = str(uuid4())
        conn.execute(jobs.insert().values(id=job_id, station=station, status="queued", created_at=now(), expires_at=now() + timedelta(hours=24)))
    return {"id": job_id, "status": "queued"}

def get_job(job_id):
    with database().connect() as conn:
        job = conn.execute(select(jobs).where(jobs.c.id == job_id, jobs.c.expires_at > now())).mappings().first()
    if not job:
        raise HTTPException(404, "File tidak ditemukan atau sudah kedaluwarsa")
    return job


@router.get("/v1/stations/{station}/export-range")
def export_range(station: str, hours: int = Query(24, ge=1, le=168), sensor: str | None = None, start: datetime | None = None, end: datetime | None = None):
    import tempfile
    import xlsxwriter
    from pathlib import Path
    from starlette.background import BackgroundTask
    from monitoring.store import observations as obs, WIB
    require_station(station)
    db = database()
    end = timestamp(end) if end is not None else latest_time(db, station, sensor=sensor)
    if end is None:
        raise HTTPException(409, "Belum ada data untuk diunduh")
    start = timestamp(start) if start is not None else end - timedelta(hours=hours)
    if start > end:
        raise HTTPException(422, "Tanggal mulai harus sebelum tanggal akhir")
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    handle, filename = tempfile.mkstemp(suffix=".xlsx", dir=EXPORT_DIR)
    os.close(handle)
    path = Path(filename)
    try:
        with xlsxwriter.Workbook(str(path), {"constant_memory": True, "strings_to_formulas": False, "strings_to_urls": False}) as book:
            sheet = book.add_worksheet("Data pengamatan")
            date_format = book.add_format({"num_format": "dd mmm yyyy hh:mm:ss"})
            sheet.write_row(0, 0, ["Waktu (WIB)", "Sensor", "Parameter", "Nilai", "Satuan"])
            sheet.freeze_panes(1, 0)
            sheet.set_column(0, 0, 24)
            sheet.set_column(1, 4, 20)
            with db.connect() as conn:
                rows = conn.execute(select(obs).where(obs.c.station == station,
                    obs.c.observed_at >= start, obs.c.observed_at <= end)
                    .order_by(obs.c.observed_at, obs.c.id)).mappings()
                for index, row in enumerate(rows, 1):
                    if index >= 1048576:
                        raise HTTPException(413, "Data terlalu besar; pilih rentang lebih pendek")
                    local = row["observed_at"].replace(tzinfo=__import__('datetime').timezone.utc).astimezone(WIB).replace(tzinfo=None)
                    sheet.write_datetime(index, 0, local, date_format)
                    sheet.write_row(index, 1, [row["sensor"], row["metric"], row["value"], "mm" if row["metric"] == "rainfall" and row["sensor"] != "jumlah_tip" else row["unit"]])
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return FileResponse(path, filename=f"{station}_{hours}jam.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        background=BackgroundTask(path.unlink, missing_ok=True))

@router.get("/v1/exports/{job_id}")
def export_status(job_id: str):
    job = get_job(job_id)
    return {"id": job_id, "status": job["status"], "error": job["error"], "expiresAt": iso(job["expires_at"])}

@router.get("/v1/exports/{job_id}/file")
def export_file(job_id: str):
    job = get_job(job_id)
    path = EXPORT_DIR / (job["id"] + ".xlsx")
    if job["status"] != "ready" or not path.is_file():
        raise HTTPException(409, "File belum siap")
    return FileResponse(path, filename=job["filename"], media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@router.get("/surabaya/snapshot")
def surabaya_snapshot(include_history: bool = True):
    """Compatibility endpoint for the existing Next.js integration."""
    result = []
    for loc in live_config()["locations"]:
        item = snapshot(database(), loc["key"], include_history=include_history)
        sensors = []
        for column in loc["water_level"]["columns"]:
            series = next((s for s in item["series"] if s["sensor"] == column), None)
            points = series["points"] if series else []
            sensors.append({"id": column, "valueCm": points[-1]["value"] if points else None,
                "referenceCm": series["referenceCm"] if series else None,
                "waterLevelCm": points[-1]["waterLevelCm"] if points else None,
                "capacityPercent": points[-1]["capacityPercent"] if points else None,
                "history": [{"time": p["time"], "valueCm": p["value"], "waterLevelCm": p["waterLevelCm"]} for p in points],
                "forecast": {"method": "unavailable", "issuedAt": None, "reason": "Prediksi belum diaktifkan", "points": []}})
        rain = None
        telemetry = {}
        rain_cols = (loc.get("rainfall") or {}).get("columns", [])
        for series in item["series"]:
            if not series["points"]:
                continue
            point = series["points"][-1]
            if series["sensor"] in rain_cols:
                if rain is None:
                    rain = {"observedAt": point["time"], "values": {}, "error": None}
                rain["values"][series["sensor"]] = point["value"]
            elif series["sensor"] not in loc["water_level"]["columns"]:
                telemetry[series["sensor"]] = point["value"]
                telemetry["observedAt"] = point["time"]
        state = item["state"]
        result.append({**item, "coordinateSource": "configured", "coordinateVerified": False,
            "lastSyncAt": max((s["lastSuccess"] for s in item["sources"] if s["lastSuccess"]), default=None),
            "state": state, "error": next((s["error"] for s in item["sources"] if s["error"]), None),
            "sensors": sensors, "rainfall": rain, "telemetry": telemetry or None})
    return {"generatedAt": iso(now()), "unit": "cm", "verification": "unverified", "pollSeconds": 30, "stations": result}
