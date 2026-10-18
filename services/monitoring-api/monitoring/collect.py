"""Read-only collectors. Never write into telemetry or Laravel databases."""
import hashlib
import json
import math
import os
import re
from datetime import timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

import openpyxl
import pymysql
import yaml
from sqlalchemy import select

from monitoring.catalog import ROOT, live_config, measurement, slug
from monitoring.store import (imports, now, timestamp, observations, save_readings,
                              read_source, set_source, upsert)

def finite(value):
    if value is None or value == "":
        return None
    try:
        number = float(str(value).replace(",", "."))
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None

def record(station, sensor, metric, time, value, unit, source, record_key, verification):
    return {"station": station, "sensor": sensor, "metric": metric, "observed_at": timestamp(time),
            "received_at": now(), "value": finite(value), "unit": unit, "source": source,
            "record_key": str(record_key), "verification": verification}

def import_excel(db, path):
    path = Path(path)
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    with db.connect() as conn:
        duplicate = conn.execute(select(imports.c.checksum).where(imports.c.checksum == checksum)).first()
    if duplicate:
        set_source(db, "excel:welang", last_success=now(), error=None)
        return {"duplicate": True, "checksum": checksum}
    source_id = "excel:welang"
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    count = skipped = 0
    reasons = []
    try:
        sheet = workbook.worksheets[0]
        rows = sheet.iter_rows(values_only=True)
        headers = [str(v or "").strip() for v in next(rows)]
        normalized = [h.lower() for h in headers]
        time_index = next((i for i, h in enumerate(normalized) if h in ("datetime", "tanggal", "time")), None)
        if time_index is None:
            raise ValueError("Kolom waktu tidak ditemukan")
        legacy = {"LP": ("welang-purwodadi", "water_level", "m"),
                  "LD": ("welang-dhompo", "water_level", "m"),
                  "RC": ("welang-rain-cendono", "rainfall", "mm"),
                  "RL": ("welang-rain-lawang", "rainfall", "mm")}
        from monitoring.catalog import stations
        canonical = {s["name"].lower(): s["id"] for s in stations() if s["region"] == "welang" and s["kind"] == "water_level"}
        mapping = {}
        for i, header in enumerate(headers):
            if header.upper() in legacy:
                mapping[i] = legacy[header.upper()]
            elif header.lower() in canonical:
                mapping[i] = (canonical[header.lower()], "water_level", "m")
        if not mapping:
            raise ValueError("Tidak ada kolom stasiun yang dikenal")
        batch = []
        for index, row in enumerate(rows, 2):
            try:
                raw_time = row[time_index]
                if isinstance(raw_time, (int, float)):
                    raw_time = openpyxl.utils.datetime.from_excel(raw_time, workbook.epoch)
                observed = timestamp(raw_time)
                for column, (station, metric, unit) in mapping.items():
                    raw = row[column] if column < len(row) else None
                    if raw not in (None, "") and finite(raw) is None:
                        raise ValueError("nilai numerik tidak valid")
                for column, (station, metric, unit) in mapping.items():
                    batch.append(record(station, metric, metric, observed.replace(tzinfo=timezone.utc),
                        row[column] if column < len(row) else None, unit, source_id,
                        station + ":" + observed.isoformat(), "confirmed_by_owner"))
                count += 1
                if len(batch) >= 400:
                    save_readings(db, batch)
                    batch.clear()
            except (ValueError, TypeError, IndexError, OverflowError):
                skipped += 1
                if len(reasons) < 50:
                    reasons.append(index)
        save_readings(db, batch)
    finally:
        workbook.close()
    upsert(db, imports, [{"checksum": checksum, "filename": path.name, "finished_at": now(),
        "inserted": count, "skipped": skipped, "details": json.dumps({"invalidRows": reasons})}], ("checksum",))
    set_source(db, source_id, last_success=now(), error=None, detail=json.dumps({"rows": count, "skipped": skipped}))
    return {"rows": count, "skipped": skipped}

def identifier(value):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError("Invalid source identifier")
    return "`" + value + "`"

def persist_source(db, key, entry, rows):
    spec = entry["spec"]
    batch, invalid = [], 0
    for row in rows:
        try:
            timestamp(row[spec["timestamp_column"]])
            for station, kind, cols in entry["targets"]:
                for col in cols:
                    meta = measurement(station, col) if kind == "water" else {"metric": "rainfall" if kind == "rain" else col, "unit": "unverified", "verification": "unverified"}
                    batch.append(record(station, col, meta["metric"], row[spec["timestamp_column"]],
                        row[col], meta["unit"], key, row[spec["id_column"]], meta["verification"]))
        except (ValueError, TypeError, OverflowError):
            invalid += 1
    save_readings(db, batch)
    return invalid

def sync_surabaya(db):
    config_path = Path(os.getenv("SURABAYA_DATABASE_CONFIG", str(ROOT / "configs/surabaya/database.local.yaml")))
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.is_file() else {}
    all_sources = {}
    for loc in live_config()["locations"]:
        groups = [("water", loc["water_level"])]
        if loc.get("rainfall"):
            groups.append(("rain", loc["rainfall"]))
        groups += [("telemetry", item) for item in (loc.get("telemetry") or [])]
        for kind, spec in groups:
            key = "mysql:" + loc["database"] + "." + spec["table"]
            entry = all_sources.setdefault(key, {"database": loc["database"], "spec": spec, "columns": set(), "targets": []})
            entry["columns"].update(spec["columns"])
            entry["targets"].append((loc["key"], kind, spec["columns"]))
    connection = None
    try:
        if not all(cfg.get(k) for k in ("host", "username", "password")):
            raise ValueError("Missing database configuration")
        connection = pymysql.connect(host=cfg["host"], port=int(cfg.get("port", 3306)),
            user=cfg["username"], password=str(cfg["password"]), charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor, connect_timeout=10, read_timeout=20, write_timeout=10)
        with connection.cursor() as cur:
            cur.execute("SET SESSION time_zone = '+07:00'")
            cur.execute("SET SESSION TRANSACTION READ ONLY")
            for key, entry in all_sources.items():
                try:
                    spec = entry["spec"]
                    table = identifier(entry["database"]) + "." + identifier(spec["table"])
                    pk, time_col = identifier(spec["id_column"]), identifier(spec["timestamp_column"])
                    state = read_source(db, key)
                    cursor = state.get("cursor") or 0
                    progress = json.loads(state.get("detail") or "{}")
                    columns = list(dict.fromkeys([spec["id_column"], spec["timestamp_column"], *sorted(entry["columns"])]))
                    query = "SELECT " + ",".join(map(identifier, columns)) + " FROM " + table
                    invalid = 0

                    if not cursor:
                        # Bootstrap current readings immediately; archive backfill has its own cursor.
                        cur.execute(query + f" ORDER BY {pk} DESC LIMIT 500")
                        rows = cur.fetchall()
                        invalid += persist_source(db, key, entry, rows)
                        if rows:
                            cursor = max(int(row[spec["id_column"]]) for row in rows)
                            newest = max(timestamp(row[spec["timestamp_column"]]) for row in rows)
                            from monitoring.store import WIB
                            cutoff = (newest - timedelta(days=366)).replace(tzinfo=timezone.utc).astimezone(WIB).replace(tzinfo=None)
                            progress = {"backfill_cursor": min(int(row[spec['id_column']]) for row in rows), "backfill_until": cursor, "cutoff": cutoff.isoformat(), "backfill_complete": False, "direction": "desc"}
                    else:
                        cur.execute(query + f" WHERE {pk} > %s ORDER BY {pk} ASC LIMIT 2000", (cursor,))
                        rows = cur.fetchall()
                        invalid += persist_source(db, key, entry, rows)
                        if rows:
                            cursor = int(rows[-1][spec["id_column"]])
                    progress["invalid_rows_last_sync"] = invalid
                    set_source(db, key, cursor=cursor, last_success=now(), error=None, detail=json.dumps(progress))
                    connection.rollback()
                except Exception:
                    connection.rollback()
                    set_source(db, key, error="Pembacaan sumber gagal; arsip tetap tersedia.")
            # Only backfill after all sources have had their live turn.
            for key, entry in all_sources.items():
                state = read_source(db, key)
                progress = json.loads(state.get("detail") or "{}")
                if not progress or progress.get("backfill_complete", True):
                    continue
                if progress.get("direction") != "desc":
                    progress["backfill_cursor"] = progress["backfill_until"] + 1
                    progress["direction"] = "desc"
                try:
                    spec = entry["spec"]
                    table = identifier(entry["database"]) + "." + identifier(spec["table"])
                    pk, time_col = identifier(spec["id_column"]), identifier(spec["timestamp_column"])
                    columns = list(dict.fromkeys([spec["id_column"], spec["timestamp_column"], *sorted(entry["columns"])]))
                    query = "SELECT " + ",".join(map(identifier, columns)) + " FROM " + table
                    batches = max(1, min(20, int(os.getenv("BACKFILL_BATCHES_PER_CYCLE", "1"))))
                    for _ in range(batches):
                        cur.execute(query + f" WHERE {pk} < %s AND {pk} <= %s AND {time_col} >= %s ORDER BY {pk} DESC LIMIT 2000",
                            (progress["backfill_cursor"], progress["backfill_until"], progress["cutoff"]))
                        historical = cur.fetchall()
                        persist_source(db, key, entry, historical)
                        if historical:
                            progress["backfill_cursor"] = min(int(row[spec["id_column"]]) for row in historical)
                        if len(historical) < 2000:
                            progress["backfill_complete"] = True
                            break
                    set_source(db, key, detail=json.dumps(progress))
                    connection.rollback()
                except Exception:
                    connection.rollback()
                    set_source(db, key, error="Pengambilan riwayat gagal; pembacaan terakhir tetap tersedia.")
    except Exception:
        for key in all_sources:
            set_source(db, key, error="Sumber MySQL belum terhubung; arsip tetap tersedia.")
    finally:
        if connection:
            connection.rollback()
            connection.close()

def sync_welang_api(db):
    """Normalized provider contract: {records:[{station,sensor,metric,time,value,unit,id}]}.

    Supply an upstream adapter when a vendor uses another schema. No vendor URL
    or proprietary field mapping is guessed here.
    """
    url = os.getenv("WELANG_API_URL", "")
    if not url:
        return
    from monitoring.catalog import get_station
    from urllib.parse import urlencode
    source = "api:welang"
    state = read_source(db, source)
    headers = {"Accept": "application/json"}
    if os.getenv("WELANG_API_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["WELANG_API_TOKEN"]
    try:
        query = urlencode({"since": state.get("detail") or ""})
        with urlopen(Request(url + ("&" if "?" in url else "?") + query, headers=headers), timeout=15) as response:
            payload = json.load(response)
        batch = []
        for item in payload["records"]:
            station = get_station(item["station"])
            if not station or station["region"] != "welang":
                raise ValueError("Unknown Welang station")
            if item["unit"] not in ("m", "cm", "mm") or not item.get("id"):
                raise ValueError("Unit and stable record ID required")
            batch.append(record(item["station"], item["sensor"], item["metric"], item["time"], item["value"],
                item["unit"], source, item["station"] + ":" + str(item["id"]), "provider_declared"))
        save_readings(db, batch)
        set_source(db, source, last_success=now(), error=None, detail=str(payload.get("next_cursor", state.get("detail") or "")))
    except Exception:
        set_source(db, source, error="API Welang belum terhubung; arsip dan Excel tetap tersedia.")
