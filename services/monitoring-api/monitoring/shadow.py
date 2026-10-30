"""Hourly, immutable shadow forecasts. UTC internally; cm water-level outputs."""
import json
import math
import os
from collections import defaultdict
from datetime import timedelta
from statistics import median
from sqlalchemy import Table, Column, String, DateTime, Float, Integer, Text, select, update
from monitoring.store import metadata, observations as obs, now, iso
from monitoring.catalog import live_config

VERSION = "surabaya-baselines-v1"
EXCLUDED_STATIONS = {"lokasi_2_kalibokor": "insufficient_validation_samples"}
runs = Table("shadow_runs", metadata,
    Column("id", String(180), primary_key=True), Column("station", String(80), nullable=False),
    Column("sensor", String(80), nullable=False), Column("origin", DateTime, nullable=False, index=True),
    Column("issued_at", DateTime, nullable=False), Column("version", String(60), nullable=False),
    Column("status", String(30), nullable=False), Column("reason", String(120)), Column("inputs", Text))
forecasts = Table("shadow_forecasts", metadata,
    Column("id", String(220), primary_key=True), Column("run_id", String(180), nullable=False, index=True),
    Column("station", String(80), nullable=False), Column("sensor", String(80), nullable=False),
    Column("model", String(40), nullable=False), Column("horizon", Integer, nullable=False),
    Column("target", DateTime, nullable=False, index=True), Column("predicted_cm", Float, nullable=False),
    Column("status", String(30), nullable=False, index=True), Column("actual_cm", Float),
    Column("evaluated_at", DateTime), Column("error_cm", Float), Column("actual_detail", Text))


# Preview replays are deliberately separate from operational verification tables.
preview_history = Table("shadow_preview_history", metadata,
    Column("id", String(80), primary_key=True), Column("snapshot_id", String(64), nullable=False, index=True),
    Column("station", String(80), nullable=False, index=True), Column("sensor", String(80), nullable=False),
    Column("origin", DateTime, nullable=False), Column("computed_at", DateTime, nullable=False),
    Column("version", String(60), nullable=False), Column("model", String(40), nullable=False),
    Column("horizon", Integer, nullable=False), Column("target", DateTime, nullable=False, index=True),
    Column("predicted_cm", Float, nullable=False), Column("inputs", Text, nullable=False))

def save_preview(db, station, sensor, origin, clock, reference, values, predicted):
    import hashlib
    from sqlalchemy.exc import IntegrityError
    inputs=json.dumps({"values_cm":values,"reference_cm":reference,"tau_hours":1},sort_keys=True)
    snapshot=hashlib.sha256(f"{VERSION}:{station}:{sensor}:{origin.isoformat()}:{inputs}".encode()).hexdigest()
    with db.connect() as conn:
        if conn.execute(select(preview_history.c.id).where(preview_history.c.snapshot_id==snapshot).limit(1)).first():
            return snapshot
    entries=[]
    for model,points in predicted.items():
        for h,value in enumerate(points,1):
            entries.append(dict(id=f"{snapshot}:{'p' if model=='persistence' else 't'}:{h}",snapshot_id=snapshot,
                station=station,sensor=sensor,origin=origin,computed_at=clock,version=VERSION,model=model,
                horizon=h,target=origin+timedelta(hours=h),predicted_cm=value,inputs=inputs))
    try:
        with db.begin() as conn: conn.execute(preview_history.insert(),entries)
    except IntegrityError:
        # A concurrent request may have saved the identical immutable snapshot.
        with db.connect() as conn:
            if not conn.execute(select(preview_history.c.id).where(preview_history.c.snapshot_id==snapshot).limit(1)).first(): raise
    return snapshot


def enabled():
    return os.getenv("SHADOW_FORECAST_ENABLED", "false").lower() == "true"

def bins(rows, reference, end):
    """Causal QC, exact duplicates counted once; 5-minute right-labelled bins."""
    by_time = defaultdict(list)
    for row in rows:
        by_time[row["observed_at"]].append(row["value"])
    grouped = defaultdict(list)
    last_value = last_time = start = None
    for stamp in sorted(by_time):
        values = by_time[stamp]
        conflict = len(set(values)) != 1
        value = values[0]
        finite = value is not None and math.isfinite(value)
        if conflict or not finite or value != last_value or last_time is None or stamp-last_time > timedelta(minutes=5):
            start = stamp
        frozen = finite and value > 0 and stamp-start >= timedelta(hours=6)
        valid = finite and 0 < value <= reference and not conflict and not frozen
        label = stamp.replace(second=0, microsecond=0, minute=stamp.minute//5*5)+timedelta(minutes=5)
        if label <= end:
            grouped[label].append((value, valid))
        last_value, last_time = value, stamp
    result = {}
    for label, values in grouped.items():
        good = [value for value, valid in values if valid]
        result[label] = reference-median(good) if len(good)>=5 and len(good)==len(values) else None
    return result

def read_rows(db, station, sensor, start, end, received_before):
    from monitoring.store import cloud_mode
    if cloud_mode() and station.startswith("lokasi_"):
        from monitoring.cloud import raw_records
        return list(raw_records(station,start,end-timedelta(microseconds=1),sensor=sensor))
    with db.connect() as conn:
        return conn.execute(select(obs).where(obs.c.station==station, obs.c.sensor==sensor,
            obs.c.observed_at>=start, obs.c.observed_at<end,
            obs.c.received_at<=received_before).order_by(obs.c.observed_at, obs.c.id)).mappings().all()

def make_predictions(values, reference):
    last=values[-1]
    x=[i/12 for i in range(13)]
    avg=sum(values[-13:])/13
    slope=sum((a-.5)*(b-avg) for a,b in zip(x,values[-13:]))/sum((a-.5)**2 for a in x)
    return {model:[max(0,min(reference,last+(slope*(1-math.exp(-h)) if model=="damped_trend" else 0))) for h in range(1,6)] for model in ("persistence","damped_trend")}

def cycle(db, clock=None, force_issue=False):
    clock=clock or now()
    origin=clock.replace(minute=clock.minute//5*5 if force_issue else 0,second=0,microsecond=0)
    # Never issue historical forecasts after a prolonged outage or startup.
    if force_issue or clock-origin<=timedelta(minutes=10):
        for location in live_config()["locations"]:
            if location["key"] in EXCLUDED_STATIONS:
                continue
            station=location["key"]; spec=location["water_level"]; ref=spec["reference_cm"]
            for sensor in spec["columns"]:
                run_id=f"{VERSION}:{station}:{sensor}:{origin.isoformat()}"
                with db.connect() as conn:
                    if conn.execute(select(runs.c.id).where(runs.c.id==run_id)).first(): continue
                rows=read_rows(db,station,sensor,origin-timedelta(hours=12),origin,clock)
                grid=bins(rows,ref,origin)
                labels=[origin-timedelta(minutes=5*i) for i in range(71,-1,-1)]
                values=[grid.get(t) for t in labels]
                valid=all(v is not None for v in values)
                detail={"reference_cm":ref,"input_start":iso(origin-timedelta(hours=6)),"input_end":iso(origin),
                    "values_cm":values,"source_max_id":max((r["id"] for r in rows),default=None),
                    "trigger":"manual" if force_issue else "scheduled","tau_hours":1,"qc":"zero/negative/above-reference/conflict/frozen-6h excluded; >=5 unique samples/bin; no imputation"}
                with db.begin() as conn:
                    conn.execute(runs.insert().values(id=run_id,station=station,sensor=sensor,origin=origin,
                        issued_at=clock,version=VERSION,status="issued" if valid else "skipped",
                        reason=None if valid else "incomplete_or_invalid_6h_input",inputs=json.dumps(detail)))
                    if valid:
                        for model,predicted in make_predictions(values,ref).items():
                            for h,value in enumerate(predicted,1):
                                conn.execute(forecasts.insert().values(id=f"{run_id}:{model}:{h}",run_id=run_id,
                                    station=station,sensor=sensor,model=model,horizon=h,target=origin+timedelta(hours=h),
                                    predicted_cm=value,status="pending"))
    with db.connect() as conn:
        due=conn.execute(select(forecasts).where(forecasts.c.status=="pending",forecasts.c.target<=clock-timedelta(minutes=10)).limit(500)).mappings().all()
    cache={}
    for forecast in due:
        key=(forecast["run_id"],forecast["target"])
        if key not in cache:
            with db.connect() as conn:
                run=conn.execute(select(runs).where(runs.c.id==forecast["run_id"])).mappings().one()
            reference=json.loads(run["inputs"])["reference_cm"]
            target=forecast["target"]
            rows=read_rows(db,forecast["station"],forecast["sensor"],target-timedelta(hours=12),target,clock)
            actual=bins(rows,reference,target).get(target)
            cache[key]=(actual,max((r["id"] for r in rows),default=None))
        actual,max_id=cache[key]
        if actual is None and clock-forecast["target"]<timedelta(hours=24): continue
        with db.begin() as conn:
            conn.execute(update(forecasts).where(forecasts.c.id==forecast["id"],forecasts.c.status=="pending").values(
                status="scored" if actual is not None else "unavailable",actual_cm=actual,evaluated_at=clock,
                error_cm=forecast["predicted_cm"]-actual if actual is not None else None,
                actual_detail=json.dumps({"source_max_id":max_id,"reason":None if actual is not None else "no_valid_target_within_24h"})))

def report(db):
    with db.connect() as conn:
        rows=conn.execute(select(forecasts)).mappings().all()
        counts=defaultdict(int)
        for row in conn.execute(select(runs.c.status)).scalars(): counts[row]+=1
    groups=defaultdict(list)
    for row in rows: groups[(row["station"],row["sensor"],row["model"],row["horizon"])].append(row)
    metrics=[]
    for (station,sensor,model,horizon),items in groups.items():
        scored=[r for r in items if r["status"]=="scored"]; errors=[r["error_cm"] for r in scored]; n=len(errors)
        days=len({r["target"].date() for r in scored})
        metrics.append(dict(evaluation_days=days,limited=n<30 or days<3,station=station,sensor=sensor,model=model,horizon=horizon,issued=len(items),scored=n,
            pending=sum(r["status"]=="pending" for r in items),unavailable=sum(r["status"]=="unavailable" for r in items),
            mae_cm=sum(abs(e) for e in errors)/n if n else None,rmse_cm=math.sqrt(sum(e*e for e in errors)/n) if n else None,
            bias_cm=sum(errors)/n if n else None))
    return {"mode":"shadow","version":VERSION,"enabled":enabled(),"excluded_stations":EXCLUDED_STATIONS,"runs":dict(counts),"metrics":metrics}


def latest_available_preview(db, station, sensor):
    """Retrospective preview only: never inserted into operational forecast metrics."""
    from monitoring.queries import latest_time
    from monitoring.catalog import get_station
    clock=now()
    from monitoring.store import cloud_mode
    if cloud_mode():
        from monitoring.cloud import latest_source_time
        try: last=latest_source_time(station,sensor)
        except Exception:
            return {"status":"unavailable","reason":"Sumber MySQL belum terhubung untuk menghitung prediksi; riwayat tersimpan tetap tersedia.","points":[]}
    else: last=latest_time(db,station,sensor)
    if last is None:
        return {"status":"waiting","reason":"Belum ada pembacaan sensor untuk membuat prediksi.","points":[]}
    # Use the last fully closed bin, not a partial bin containing the last sample.
    origin=last.replace(second=0,microsecond=0,minute=last.minute//5*5)
    location=next((loc for loc in live_config()["locations"] if loc["key"]==station),None)
    if location is None or sensor not in location["water_level"]["columns"]:
        return {"status":"unavailable","reason":"Sensor prediksi tidak tersedia.","points":[]}
    ref=location["water_level"]["reference_cm"]
    grid=bins(read_rows(db,station,sensor,origin-timedelta(hours=12),origin,clock),ref,origin)
    values=[grid.get(origin-timedelta(minutes=5*i)) for i in range(71,-1,-1)]
    valid=sum(v is not None for v in values)
    if valid!=72:
        return {"status":"skipped","reason":f"Riwayat 6 jam sebelum data terakhir belum memenuhi syarat ({valid}/72 interval valid).", "observedAt":iso(last),"origin":iso(origin),"points":[]}
    predicted=make_predictions(values,ref)
    snapshot=save_preview(db,station,sensor,origin,clock,ref,values,predicted)
    points=[{"hours":h,"target":iso(origin+timedelta(hours=h)),"persistenceCm":predicted["persistence"][h-1],"trendCm":predicted["damped_trend"][h-1]} for h in range(1,6)]
    return {"status":"issued","mode":"last_available_preview","historical":origin+timedelta(hours=1)<=clock,
        "snapshotId":snapshot,"computedAt":iso(clock),"observedAt":iso(last),"origin":iso(origin),"originValueCm":values[-1],"version":VERSION,"points":points}


def export_prediction_sheet(book, db, station, start, end, date_format):
    from monitoring.store import WIB
    from datetime import timezone
    sheet=book.add_worksheet("Prediksi")
    headers=["Waktu acuan (WIB)","Waktu target (WIB)","Dihitung (WIB)","Sensor","Model","Horizon (jam)","Prediksi muka air","Satuan","Jenis","Versi model","Status evaluasi","Aktual (cm)","ID prediksi"]
    sheet.write_row(0,0,headers); sheet.freeze_panes(1,0); sheet.set_column(0,2,24); sheet.set_column(3,12,22)
    records=[]
    with db.connect() as conn:
        for row in conn.execute(select(preview_history).where(preview_history.c.station==station,
            preview_history.c.target>=start,preview_history.c.target<=end)).mappings():
            records.append((row['origin'],row['target'],row['computed_at'],row['sensor'],row['model'],row['horizon'],row['predicted_cm'],'cm','Replay data terakhir',row['version'],'Tidak dievaluasi operasional',None,row['id']))
        query=select(forecasts,runs.c.origin,runs.c.issued_at,runs.c.version).join(runs,forecasts.c.run_id==runs.c.id).where(forecasts.c.station==station,forecasts.c.target>=start,forecasts.c.target<=end)
        for row in conn.execute(query).mappings():
            records.append((row['origin'],row['target'],row['issued_at'],row['sensor'],row['model'],row['horizon'],row['predicted_cm'],'cm','Operasional internal',row['version'],row['status'],row['actual_cm'],row['id']))
    for i,record in enumerate(sorted(records,key=lambda x:(x[1],x[3],x[4],x[2])),1):
        if i>=1048576: raise ValueError("Terlalu banyak prediksi untuk satu sheet")
        for c,value in enumerate(record):
            if c<3: sheet.write_datetime(i,c,value.replace(tzinfo=timezone.utc).astimezone(WIB).replace(tzinfo=None),date_format)
            else: sheet.write(i,c,value)
    if records: sheet.autofilter(0,0,len(records),len(headers)-1)
    else: sheet.write(1,0,"Belum ada prediksi tersimpan dengan waktu target pada rentang ini.")
