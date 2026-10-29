from datetime import timedelta

from sqlalchemy import select, func
from monitoring.catalog import get_station, measurement
from monitoring.store import observations as obs, sources, iso, now

def latest_time(db, station, sensor=None):
    filters = [obs.c.station == station]
    if sensor is not None:
        filters.append(obs.c.sensor == sensor)
    with db.connect() as conn:
        return conn.execute(select(func.max(obs.c.observed_at)).where(*filters)).scalar()

def preferred_rows(rows):
    # Keep source records intact in storage; choose API over Excel only for display.
    chosen = {}
    for row in rows:
        key = (row["observed_at"], row["sensor"], row["metric"])
        prior = chosen.get(key)
        rank = (0 if row["source"].startswith("excel:") else 1, row["received_at"], row["id"])
        if prior is None or rank > prior[0]:
            chosen[key] = (rank, row)
    return [item[1] for _, item in sorted(chosen.items())]

def format_series(rows, station, max_points=2000):
    groups = {}
    specifications = {}
    for row in preferred_rows(rows):
        row = dict(row)
        if station.startswith("welang-") and row["metric"] == "water_level" and row["unit"] == "cm":
            row["value"] = row["value"] / 100 if row["value"] is not None else None
            row["unit"] = "m"
        key = row["sensor"] + ":" + row["metric"]
        if row["sensor"] not in specifications:
            specifications[row["sensor"]] = measurement(station, row["sensor"])
        spec = specifications[row["sensor"]]
        is_water = row["metric"] in ("water_level", "distance")
        reference = spec["referenceCm"] if is_water else None
        interpretation = "distance_to_surface" if is_water and spec.get("measurement") == "distance_to_surface" else "water_level" if is_water else None
        value_cm = row["value"] * (100 if row["unit"] == "m" else 1) if is_water and row["value"] is not None and row["unit"] in ("cm", "m") else None
        water_cm = max(0, reference - value_cm) if interpretation == "distance_to_surface" and reference is not None and value_cm is not None else value_cm if interpretation == "water_level" else None
        series = groups.setdefault(key, {"sensor": row["sensor"], "metric": row["metric"],
            "unit": row["unit"], "verification": row["verification"],
            "measurement": interpretation, "referenceCm": reference, "points": []})
        capacity = min(100, max(0, water_cm / reference * 100)) if reference is not None and reference > 0 and water_cm is not None else None
        series["points"].append({"time": iso(row["observed_at"]), "value": row["value"], "source": row["source"], "waterLevelCm": water_cm, "capacityPercent": capacity})
    for series in groups.values():
        points = series["points"]
        if len(points) > max_points:
            # Bucket extrema retain spikes; the downloadable archive is never downsampled.
            size = max(1, (len(points) + max_points // 2 - 1) // (max_points // 2))
            reduced = []
            for offset in range(0, len(points), size):
                bucket = points[offset:offset + size]
                valid = [p for p in bucket if p["value"] is not None]
                candidates = [min(valid, key=lambda p: p["value"]), max(valid, key=lambda p: p["value"])] if valid else bucket[:1]
                reduced.extend(sorted({p["time"]: p for p in candidates}.values(), key=lambda p: p["time"]))
            series["points"] = sorted({p["time"]: p for p in [points[0], *reduced, points[-1]]}.values(), key=lambda p: p["time"])
    return list(groups.values())

def history(db, station, start, end, max_points=2000, sensor=None):
    filters = [obs.c.station == station, obs.c.observed_at >= start, obs.c.observed_at <= end]
    if sensor is not None:
        filters.append(obs.c.sensor == sensor)
    with db.connect() as conn:
        rows = conn.execute(select(obs).where(*filters).order_by(obs.c.observed_at, obs.c.id)).mappings().all()
    return {"station": station, "from": iso(start), "to": iso(end), "series": format_series(rows, station, max_points)}

def latest_series(db, station):
    if db.dialect.name == "sqlite":
        # Seek between signals instead of scanning the full station archive.
        rows = []
        previous = None
        with db.connect() as conn:
            while True:
                query = select(obs.c.sensor, obs.c.metric).where(obs.c.station == station)
                signal = None
                if previous is not None:
                    signal = conn.execute(query.where(obs.c.sensor == previous[0],
                        obs.c.metric > previous[1]).order_by(obs.c.metric).limit(1)).first()
                    query = query.where(obs.c.sensor > previous[0])
                if signal is None:
                    signal = conn.execute(query.order_by(obs.c.sensor, obs.c.metric).limit(1)).first()
                if signal is None:
                    break
                previous = tuple(signal)
                row = conn.execute(select(obs).where(
                    obs.c.station == station, obs.c.sensor == signal.sensor,
                    obs.c.metric == signal.metric).order_by(
                        obs.c.observed_at.desc(), obs.c.id.desc()).limit(1)).mappings().first()
                if row is not None:
                    rows.append(row)
        return format_series(rows, station)
    # Include the full index prefix in GROUP BY so MySQL can skip directly
    # between signals rather than scanning every historical reading.
    latest = select(obs.c.station, obs.c.sensor, obs.c.metric,
                    func.max(obs.c.observed_at).label("latest")).where(obs.c.station == station).group_by(
                        obs.c.station, obs.c.sensor, obs.c.metric).subquery()
    with db.connect() as conn:
        rows = conn.execute(select(obs).join(latest,
            (obs.c.station == latest.c.station) & (obs.c.sensor == latest.c.sensor) &
            (obs.c.metric == latest.c.metric) & (obs.c.observed_at == latest.c.latest))).mappings().all()
    return format_series(rows, station)

def snapshot(db, station_id, include_history=False):
    station = get_station(station_id)
    if not station:
        return None
    latest = latest_time(db, station_id)
    data = history(db, station_id, latest - timedelta(hours=24), latest) if latest and include_history else {"series": latest_series(db, station_id) if latest else []}
    with db.connect() as conn:
        if db.dialect.name == "sqlite":
            source_names = []
            while True:
                query = select(obs.c.source).where(obs.c.station == station_id)
                if source_names:
                    query = query.where(obs.c.source > source_names[-1])
                source = conn.execute(query.order_by(obs.c.source).limit(1)).scalar()
                if source is None:
                    break
                source_names.append(source)
        else:
            source_names = conn.execute(select(obs.c.source).where(obs.c.station == station_id).group_by(obs.c.station, obs.c.source)).scalars().all()
        states = conn.execute(select(sources).where(sources.c.id.in_(source_names))).mappings().all() if source_names else []
    source_states = [{"id": s["id"], "lastSuccess": iso(s["last_success"]), "error": s["error"]} for s in states]
    state = "unavailable" if latest is None else "historical" if all(s.startswith("excel:") for s in source_names) else "stale" if now() - latest > timedelta(hours=24) else "live"
    if state == "live" and any(s["error"] for s in source_states):
        state = "cached"
    return {**station, "observedAt": iso(latest), "state": state,
            "sources": source_states, "series": data["series"], "sensors": sensor_catalog(station_id, data["series"])}


def sensor_catalog(station_id, series):
    from monitoring.catalog import live_config
    result = {(s["sensor"], s["metric"]): {**s, "points": []} for s in series}
    for location in live_config()["locations"]:
        if location["key"] != station_id:
            continue
        groups = [("water", location["water_level"])]
        if location.get("rainfall"):
            groups.append(("rain", location["rainfall"]))
        groups += [("telemetry", item) for item in location.get("telemetry", [])]
        for kind, spec in groups:
            for column in spec["columns"]:
                meta = measurement(station_id, column) if kind == "water" else {"metric": "rainfall" if kind == "rain" else column, "unit": "unverified", "verification": "unverified"}
                key = (column, meta["metric"])
                result.setdefault(key, {"sensor": column, **meta, "points": []})
    if station_id.startswith("welang-") and not result:
        metric = "rainfall" if station_id.startswith("welang-rain-") else "water_level"
        result[(metric, metric)] = {"sensor": metric, "metric": metric, "unit": "mm" if metric == "rainfall" else "m", "verification": "unverified", "points": []}
    return list(result.values())
