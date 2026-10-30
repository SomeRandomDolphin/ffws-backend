"""Compact cloud collector. All upstream MySQL access is read-only."""
import json
import os
from collections import defaultdict
from datetime import timedelta, timezone
from heapq import merge
from sqlalchemy import Table, Column, String, DateTime, Integer, Float, select, delete
import pymysql
import yaml
from monitoring.catalog import ROOT,live_config,measurement
from monitoring.collect import identifier,finite
from monitoring.store import metadata,observations,now,timestamp,upsert,read_source,set_source,WIB

stats=Table("aggregate_stats",metadata,
    Column("station",String(80),primary_key=True),Column("sensor",String(80),primary_key=True),
    Column("bucket",DateTime,primary_key=True),Column("minimum",Float),Column("maximum",Float),
    Column("samples",Integer,nullable=False),Column("valid_samples",Integer,nullable=False),
    Column("zero_samples",Integer,nullable=False),Column("invalid_samples",Integer,nullable=False),
    Column("median_distance",Float))

def connection():
    from pathlib import Path
    local_settings={}
    env_file=Path(os.getenv("MONITORING_SUPABASE_CONFIG",str(ROOT/".env.supabase.local")))
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                key,value=line.split("=",1); local_settings[key.strip()]=value.strip()
    def setting(key,default=None): return os.getenv(key,local_settings.get(key,default))
    if setting("MYSQL_SOURCE_HOST"):
        cfg={"host":setting("MYSQL_SOURCE_HOST"),"port":setting("MYSQL_SOURCE_PORT","3306"),
             "username":setting("MYSQL_SOURCE_USER"),"password":setting("MYSQL_SOURCE_PASSWORD")}
    else:
        path=os.getenv("SURABAYA_DATABASE_CONFIG",str(ROOT/"configs/surabaya/database.local.yaml"))
        cfg=yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    db=pymysql.connect(host=cfg['host'],port=int(cfg.get('port',3306)),user=cfg['username'],password=str(cfg['password']),
        charset='utf8mb4',connect_timeout=10,read_timeout=60,write_timeout=10)
    with db.cursor() as c:
        c.execute("SET SESSION time_zone = '+07:00'"); c.execute("SET SESSION TRANSACTION READ ONLY")
    return db

def source_groups():
    groups={}
    for loc in live_config()['locations']:
        specs=[('water',loc['water_level'])]
        if loc.get('rainfall'): specs.append(('rain',loc['rainfall']))
        specs += [('telemetry',item) for item in loc.get('telemetry',[])]
        for kind,spec in specs:
            key='mysql:'+loc['database']+'.'+spec['table']
            group=groups.setdefault(key,{'database':loc['database'],'spec':spec,'signals':{}})
            for col in spec['columns']:
                meta=measurement(loc['key'],col) if kind=='water' else {'metric':'rainfall' if kind=='rain' else col,'unit':'unverified','verification':'unverified'}
                group['signals'][(loc['key'],col)]=meta
    return groups

def floor5(t): return t.replace(minute=t.minute//5*5,second=0,microsecond=0)
def local(t): return t.replace(tzinfo=timezone.utc).astimezone(WIB).replace(tzinfo=None)

def raw_records(station,start,end,sensor=None,source_only=None):
    """Stream native source records, never substitute five-minute aggregates."""
    iterators=[]
    for key,group in source_groups().items():
        if source_only and key!=source_only: continue
        signals={(st,col):meta for (st,col),meta in group['signals'].items() if st==station and (sensor is None or sensor==col)}
        if not signals: continue
        def stream(key=key,group=group,signals=signals):
            spec=group['spec']; columns=list(dict.fromkeys([spec['id_column'],spec['timestamp_column'],*[col for st,col in signals]]))
            table=identifier(group['database'])+'.'+identifier(spec['table']); time_col=identifier(spec['timestamp_column'])
            db=connection()
            try:
                with db.cursor(pymysql.cursors.SSDictCursor) as cursor:
                    cursor.execute('SELECT '+','.join(map(identifier,columns))+' FROM '+table+' WHERE '+time_col+' >= %s AND '+time_col+' <= %s ORDER BY '+time_col+','+identifier(spec['id_column']),(local(start),local(end)))
                    for row in cursor:
                        stamp=timestamp(row[spec['timestamp_column']])
                        for (st,col),meta in signals.items():
                            yield dict(id=row[spec['id_column']],station=st,sensor=col,metric=meta['metric'],observed_at=stamp,
                                received_at=now(),value=finite(row[col]),unit=meta['unit'],source=key,record_key=str(row[spec['id_column']]),verification=meta['verification'])
            finally: db.close()
        iterators.append(stream())
    yield from merge(*iterators,key=lambda row:(row['observed_at'],row['id'],row['sensor']))

def aggregate_records(rows,source,end):
    from monitoring.shadow import bins
    from statistics import median
    water_rows=defaultdict(list)
    for row in rows:
        if measurement(row['station'],row['sensor']).get('referenceCm') is not None:
            water_rows[(row['station'],row['sensor'])].append(row)
    water_grid={key:bins(items,measurement(*key)['referenceCm'],end) for key,items in water_rows.items()}
    groups=defaultdict(list)
    for row in rows:
        label=floor5(row['observed_at'])+timedelta(minutes=5)
        if label<=end: groups[(row['station'],row['sensor'],label)].append(row)
    means=[]; details=[]
    for (station,sensor,label),items in groups.items():
        # Collapse identical timestamps; conflicting timestamps remain invalid.
        unique=defaultdict(list)
        for item in items: unique[item['observed_at']].append(item['value'])
        values=[]; valid=[]; zeros=0; invalid=0
        spec=measurement(station,sensor); ref=spec.get('referenceCm')
        for stamp,samples in unique.items():
            if len(set(samples))!=1: invalid+=1; continue
            value=samples[0]
            if value is None: invalid+=1; continue
            values.append(value)
            if value==0: zeros+=1
            if ref is None or 0<value<=ref: valid.append(value)
            else: invalid+=1
        sample=items[-1]
        level=water_grid.get((station,sensor),{}).get(label)
        model_median=median(values) if ref is not None and level is not None else None
        display_value=median(values) if ref is not None and values else sum(values)/len(values) if values else None
        means.append(dict(station=station,sensor=sensor,metric=sample['metric'],observed_at=label,received_at=now(),
            value=display_value,unit=sample['unit'],source=source,
            record_key='5m:'+label.isoformat(),verification=sample['verification']))
        details.append(dict(station=station,sensor=sensor,bucket=label,minimum=min(values) if values else None,
            maximum=max(values) if values else None,samples=len(unique),valid_samples=len(valid),zero_samples=zeros,invalid_samples=invalid,median_distance=model_median))
    return means,details

def sync(db,force=False):
    for source,group in source_groups().items():
        upstream=None
        try:
            upstream=connection(); spec=group['spec']; table=identifier(group['database'])+'.'+identifier(spec['table'])
            with upstream.cursor() as cursor:
                cursor.execute('SELECT '+identifier(spec['id_column'])+','+identifier(spec['timestamp_column'])+' FROM '+table+' ORDER BY '+identifier(spec['id_column'])+' DESC LIMIT 1')
                latest=cursor.fetchone()
            if not latest: continue
            state=read_source(db,source); previous=json.loads(state.get('detail') or '{}')
            if not force and previous.get('compact_id')==latest[0] and previous.get('aggregation_version')=='median-v2':
                set_source(db,source,last_success=now(),error=None); continue
            end=min(floor5(timestamp(latest[1])),floor5(now()))
            previous_end=previous.get('compact_end') if not force and previous.get('aggregation_version')=='median-v2' else None
            start=max(end-timedelta(days=7),__import__('datetime').datetime.fromisoformat(previous_end)-timedelta(minutes=5)) if previous_end else end-timedelta(days=7)
            all_rows=[]
            for station in {st for st,col in group['signals']}:
                all_rows.extend(row for row in raw_records(station,start-timedelta(hours=12),end,source_only=source))
            means,details=aggregate_records(all_rows,source,end)
            means=[row for row in means if row['observed_at']>start]
            details=[row for row in details if row['bucket']>start]
            for i in range(0,len(means),1000): upsert(db,observations,means[i:i+1000],('source','record_key','sensor','metric'))
            for i in range(0,len(details),1000): upsert(db,stats,details[i:i+1000],('station','sensor','bucket'))
            for station in {st for st,col in group['signals']}:
                with db.begin() as conn:
                    conn.execute(delete(observations).where(observations.c.station==station,observations.c.source==source,observations.c.observed_at<end-timedelta(days=30)))
                    for st,col in group['signals']:
                        if st==station: conn.execute(delete(stats).where(stats.c.station==st,stats.c.sensor==col,stats.c.bucket<end-timedelta(days=30)))
            set_source(db,source,cursor=latest[0],last_success=now(),error=None,detail=json.dumps({'compact_id':latest[0],'compact_end':end.isoformat(),'interval_seconds':300,'aggregation_version':'median-v2'}))
        except Exception:
            set_source(db,source,error='Sinkronisasi agregat MySQL belum berhasil; data tersimpan tetap tersedia.')
            import logging; logging.getLogger(__name__).exception('Compact source sync failed: %s',source)
        finally:
            if upstream: upstream.close()


def latest_source_time(station,sensor):
    latest=None
    for source,group in source_groups().items():
        if (station,sensor) not in group['signals']: continue
        spec=group['spec']; db=connection()
        try:
            with db.cursor() as c:
                c.execute('SELECT '+identifier(spec['timestamp_column'])+' FROM '+identifier(group['database'])+'.'+identifier(spec['table'])+' ORDER BY '+identifier(spec['id_column'])+' DESC LIMIT 1')
                row=c.fetchone()
                if row: latest=max(latest,timestamp(row[0])) if latest else timestamp(row[0])
        finally: db.close()
    return latest
