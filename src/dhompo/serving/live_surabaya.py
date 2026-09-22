"""Read-only MySQL ingestion, local history and provisional Surabaya forecasts."""
from __future__ import annotations

import copy
import json
import math
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pymysql

from dhompo.config import PROJECT_ROOT, load_yaml_config
from dhompo.data.urban_loader import QUALITY_MISSING, QUALITY_OK
from dhompo.serving.urban_file_predictor import UrbanFilePredictor

WIB = timezone(timedelta(hours=7))
CONFIG = "configs/surabaya/live_sources.yaml"


def identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError("Invalid configured SQL identifier")
    return "`" + value + "`"


def timestamp(value) -> datetime:
    dt = datetime.fromisoformat(str(value))
    return dt.replace(tzinfo=WIB) if dt.tzinfo is None else dt.astimezone(WIB)


def finite(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


class LiveSurabaya:
    def __init__(self, config=None, cache_path=None, connect=None):
        self.config = config or load_yaml_config(CONFIG)
        self.cache_path = Path(cache_path or PROJECT_ROOT / "artifacts/surabaya/live.sqlite3")
        self.connect = connect or pymysql.connect
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.snapshot_lock = threading.Lock()
        self.thread = None
        self.predictor = None
        self.model_error = None
        self.last_success = {}
        self.errors = {}
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with self.local() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS readings (
                source TEXT NOT NULL, row_id INTEGER NOT NULL,
                observed_at TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(source, row_id))""")
        self.snapshot = self.build_snapshot()

    def local(self):
        return sqlite3.connect(self.cache_path, timeout=10)

    @staticmethod
    def telemetry_specs(location):
        specs = location.get("telemetry") or []
        return specs if isinstance(specs, list) else [specs]

    def sources(self):
        result = {}
        for loc in self.config["locations"]:
            for kind in ("water_level", "rainfall"):
                spec = loc.get(kind)
                if not spec:
                    continue
                key = loc["database"] + "." + spec["table"]
                if key not in result:
                    result[key] = dict(spec, database=loc["database"], columns=[])
                result[key]["columns"] = list(dict.fromkeys(result[key]["columns"] + spec["columns"]))
            for spec in self.telemetry_specs(loc):
                key = loc["database"] + "." + spec["table"]
                if key not in result:
                    result[key] = dict(spec, database=loc["database"], columns=[])
                result[key]["columns"] = list(dict.fromkeys(result[key]["columns"] + spec["columns"]))
        return result

    def ingest(self, key, spec, rows):
        records = []
        for row in rows:
            # A malformed row must not discard the rest of a batch.
            try:
                observed = timestamp(row[spec["timestamp_column"]]).isoformat()
                values = {col: finite(row.get(col)) for col in spec["columns"]}
                records.append((key, int(row[spec["id_column"]]), observed, json.dumps(values)))
            except (ValueError, TypeError, KeyError):
                continue
        with self.local() as db:
            db.executemany("INSERT OR REPLACE INTO readings VALUES (?,?,?,?)", records)

    def read_rows(self, key, limit=6000):
        with self.local() as db:
            rows = db.execute(
                "SELECT row_id, observed_at, payload FROM readings WHERE source=? "
                "ORDER BY row_id DESC LIMIT ?", (key, limit)
            ).fetchall()
        return sorted(
            [{"id": row[0], "time": row[1], **json.loads(row[2])} for row in rows],
            key=lambda row: (row["time"], row["id"]),
        )

    def sync_once(self):
        # One worker per API process; no remote queries in HTTP handlers.
        with self.lock:
            cfg = load_yaml_config("configs/surabaya/database.local.yaml")
            sources = self.sources()
            conn = None
            try:
                if not all(cfg.get(k) for k in ("host", "username", "password")):
                    raise ValueError("Missing connection configuration")
                conn = self.connect(
                    host=cfg["host"], port=int(cfg.get("port", 3306)),
                    user=cfg["username"], password=str(cfg["password"]),
                    charset=cfg.get("charset", "utf8mb4"), connect_timeout=10,
                    read_timeout=10, write_timeout=10, autocommit=False,
                    cursorclass=pymysql.cursors.DictCursor,
                )
                with conn.cursor() as cur:
                    cur.execute("SET SESSION time_zone = '+07:00'")
                    cur.execute("SET SESSION TRANSACTION READ ONLY")
                    for key, spec in sources.items():
                        if self.stop.is_set():
                            break
                        try:
                            with self.local() as db:
                                latest_cached = db.execute(
                                    "SELECT row_id, payload FROM readings WHERE source=? ORDER BY row_id DESC LIMIT 1", (key,)
                                ).fetchone()
                            cursor = latest_cached[0] if latest_cached else None
                            cached_columns = set(json.loads(latest_cached[1])) if latest_cached else set()
                            needs_backfill = cursor is None or any(column not in cached_columns for column in spec["columns"])
                            table = identifier(spec["database"]) + "." + identifier(spec["table"])
                            pk = identifier(spec["id_column"])
                            columns = list(dict.fromkeys(
                                [spec["id_column"], spec["timestamp_column"]] + spec["columns"]
                            ))
                            sql = "SELECT " + ",".join(map(identifier, columns)) + " FROM " + table
                            # Initial tail is bounded; subsequent batches use the primary key.
                            if needs_backfill:
                                cur.execute(sql + f" ORDER BY {pk} DESC LIMIT 6000")
                            else:
                                cur.execute(sql + f" WHERE {pk} > %s ORDER BY {pk} ASC LIMIT 6000", (cursor,))
                            self.ingest(key, spec, cur.fetchall())
                            self.last_success[key] = datetime.now(WIB).isoformat()
                            self.errors.pop(key, None)
                        except Exception:
                            self.errors[key] = "Pembacaan sumber gagal; menampilkan riwayat tersimpan."
            except Exception:
                # Never expose driver exceptions, host, username or password.
                self.errors.update({key: "Sumber belum terhubung; menampilkan riwayat tersimpan." for key in sources})
            finally:
                if conn is not None:
                    try:
                        conn.rollback()
                    finally:
                        conn.close()
            snapshot = self.build_snapshot()
            with self.snapshot_lock:
                self.snapshot = snapshot

    def forecast(self, location, sensor, rows, now, source_ok):
        latest = rows[-1] if rows else None
        empty = {
            "method": "unavailable", "issuedAt": None, "points": [], "reason": "",
            "modelVersion": None, "usesPumpTelemetry": False, "dataFreshnessSeconds": None,
        }
        if not latest or finite(latest.get(sensor)) is None:
            return dict(empty, reason="Pembacaan sensor belum tersedia.")
        anchor = timestamp(latest["time"])
        age = (now - anchor).total_seconds()
        if not source_ok or age > 600 or age < -60:
            return dict(empty, reason="Prediksi ditahan: sumber terputus, data terlambat, atau waktu tidak valid.")
        value = finite(latest[sensor])
        if value <= 0:
            return dict(empty, reason="Nilai nol/negatif belum dapat diinterpretasikan untuk prediksi.")
        if not self.config["water_level"].get("predictions_enabled"):
            return dict(empty, reason="Prediksi belum diaktifkan.")
        method = "persistence"
        model_version = "persistence_v1"
        uses_pump_telemetry = False
        reason = "Baseline nilai tetap; model terlatih untuk lokasi ini belum tersedia."
        forecasts = {f"h{h}": value for h in range(1, 6)}
        intervals = {}
        if location["key"] == "lokasi_1_hang_tuah":
            if self.predictor is None:
                return dict(empty, reason="Model Hang Tuah belum siap.")
            try:
                target = self.predictor.target_column
                end = pd.Timestamp(anchor).tz_localize(None).floor("30min")
                index = pd.date_range(end=end, periods=24, freq="30min")
                series = pd.Series(
                    [finite(row.get(sensor)) for row in rows],
                    index=pd.DatetimeIndex([timestamp(row["time"]) for row in rows]).tz_localize(None),
                    dtype=float,
                ).sort_index()
                # Causal bins end at each grid timestamp; no interpolation from future data.
                series = series.resample("30min", closed="right", label="right").last().reindex(index)
                if series.isna().any() or (series <= 0).any():
                    return dict(empty, reason="Model membutuhkan 12 jam riwayat valid dengan interval 30 menit.")
                values = pd.DataFrame(float("nan"), index=index, columns=self.predictor.source_signals)
                values[target] = series
                flags = pd.DataFrame(QUALITY_MISSING, index=index, columns=values.columns)
                flags[target] = QUALITY_OK
                # Other signals have unconfirmed sensor/rainfall semantics.
                # Keep them explicitly missing rather than averaging A/B or inventing rainfall.
                result = self.predictor.predict_from_history(values, flags)
                forecasts = result.predictions
                intervals = getattr(result, "intervals", {})
                if any(finite(v) is None or v < 0 for v in forecasts.values()):
                    return dict(empty, reason="Hasil model tidak valid.")
                anchor = end.to_pydatetime().replace(tzinfo=WIB)
                method = "urban_file"
                model_version = getattr(result, "model_version", "urban_file_v1")
                uses_pump_telemetry = bool(getattr(result, "uses_pump_telemetry", False))
                fallback_horizons = getattr(result, "fallback_horizons", ())
                if fallback_horizons:
                    reason = (
                        "Persistence dipakai untuk horizon yang belum mengalahkan baseline: "
                        + ", ".join(fallback_horizons)
                        + "."
                    )
                else:
                    reason = "Model Hang Tuah eksperimental; fitur Kalibokor ditandai missing. Asumsi cm belum diverifikasi."
            except Exception:
                return dict(empty, reason="Model belum dapat menghasilkan prediksi dari riwayat ini.")
        points = []
        for horizon_key, predicted in forecasts.items():
            lead_hours = int(horizon_key[1:])
            point = {
                "leadHours": lead_hours,
                "time": (anchor + timedelta(hours=lead_hours)).isoformat(),
                "valueCm": round(predicted, 3),
            }
            if horizon_key in intervals:
                point["lowerCm"], point["upperCm"] = intervals[horizon_key]
            points.append(point)
        return {
            "method": method,
            "issuedAt": anchor.isoformat(),
            "reason": reason,
            "modelVersion": model_version,
            "usesPumpTelemetry": uses_pump_telemetry,
            "dataFreshnessSeconds": round(age, 3),
            "points": points,
        }

    def build_snapshot(self, now=None):
        now = now or datetime.now(WIB)
        stations = []
        for loc in self.config["locations"]:
            spec = loc["water_level"]
            key = loc["database"] + "." + spec["table"]
            rows = self.read_rows(key)
            latest = rows[-1] if rows else None
            age = (now - timestamp(latest["time"])).total_seconds() if latest else None
            state = ("unavailable" if latest is None else "future" if age < -60
                     else "stale" if age > 600 else "live")
            source_ok = key in self.last_success and key not in self.errors
            if not source_ok and state == "live":
                state = "cached"
            sensors = []
            for column in spec["columns"]:
                # 30-minute last observations for display, gaps stay gaps.
                history = {}
                if latest:
                    cutoff = timestamp(latest["time"]) - timedelta(hours=24)
                    for row in rows:
                        observed = timestamp(row["time"])
                        if observed >= cutoff:
                            bucket = observed.replace(minute=(observed.minute // 30)*30, second=0, microsecond=0)
                            history[bucket.isoformat()] = {"time": row["time"], "valueCm": finite(row.get(column))}
                raw_value = finite(latest.get(column)) if latest else None
                reference_cm = finite(spec.get("reference_cm"))
                water_level = (
                    max(0.0, reference_cm - raw_value)
                    if raw_value is not None and reference_cm is not None
                    else None
                )
                sensors.append({
                    "id": column, "valueCm": raw_value, "waterLevelCm": water_level,
                    "measurement": spec.get("measurement", "unverified"),
                    "calibrationRequired": bool(spec.get("calibration_required", False)),
                    "history": list(history.values()),
                    "forecast": self.forecast(loc, column, rows, now, source_ok),
                })
            rain = None
            if loc.get("rainfall"):
                rain_spec = loc["rainfall"]
                rain_key = loc["database"] + "." + rain_spec["table"]
                rain_rows = self.read_rows(rain_key, 1)
                if rain_rows:
                    rain = {"observedAt": rain_rows[-1]["time"],
                            "values": {col: rain_rows[-1].get(col) for col in rain_spec["columns"]},
                            "error": self.errors.get(rain_key)}
            telemetry = None
            telemetry_specs = self.telemetry_specs(loc)
            if telemetry_specs:
                telemetry = {"observedAt": None}
                for telemetry_spec in telemetry_specs:
                    telemetry_key = loc["database"] + "." + telemetry_spec["table"]
                    telemetry_rows = self.read_rows(telemetry_key, 1)
                    latest_telemetry = telemetry_rows[-1] if telemetry_rows else None
                    if not latest_telemetry:
                        continue
                    observed_at = latest_telemetry["time"]
                    if telemetry["observedAt"] is None or timestamp(observed_at) > timestamp(telemetry["observedAt"]):
                        telemetry["observedAt"] = observed_at
                    for column in telemetry_spec["columns"]:
                        telemetry[column] = finite(latest_telemetry.get(column))
            coords = loc.get("coordinates") or {}
            stations.append({
                "id": loc["key"], "name": loc["name"],
                "latitude": coords.get("latitude"), "longitude": coords.get("longitude"),
                "coordinateSource": coords.get("source"), "coordinateVerified": coords.get("verified", False),
                "observedAt": latest["time"] if latest else None,
                "lastSyncAt": self.last_success.get(key), "state": state,
                "error": self.errors.get(key), "sensors": sensors, "rainfall": rain, "telemetry": telemetry,
            })
        return {"generatedAt": now.isoformat(), "unit": "cm", "verification": "unverified",
                "pollSeconds": 30, "stations": stations}

    def get_snapshot(self):
        with self.snapshot_lock:
            result = copy.deepcopy(self.snapshot)
        # Recompute freshness on every read, even if the worker has stopped.
        now = datetime.now(WIB)
        for station in result["stations"]:
            if station["observedAt"] and (now - timestamp(station["observedAt"])).total_seconds() > 600:
                station["state"] = "stale"
                for sensor in station["sensors"]:
                    sensor["forecast"] = {"method": "unavailable", "issuedAt": None, "points": [],
                                          "reason": "Data terlambat; prediksi terkini tidak tersedia."}
        return result

    def start(self):
        def work():
            try:
                self.predictor = UrbanFilePredictor()
            except Exception:
                self.model_error = "Model tidak tersedia"
            while not self.stop.is_set():
                try:
                    self.sync_once()
                except Exception:
                    # A local cache error must not kill the worker or log credentials.
                    self.errors.update({key: "Sinkronisasi belum berhasil." for key in self.sources()})
                self.stop.wait(30)
        self.thread = threading.Thread(target=work, name="surabaya-sync", daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=2)
