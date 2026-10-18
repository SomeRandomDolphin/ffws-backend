import os
from datetime import datetime, timezone, timedelta

from sqlalchemy import (create_engine, MetaData, Table, Column, String, Integer,
                        Float, DateTime, Text, UniqueConstraint, Index, select, text)
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

metadata = MetaData()
observations = Table(
    "observations", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("station", String(80), nullable=False),
    Column("sensor", String(80), nullable=False),
    Column("metric", String(80), nullable=False),
    Column("observed_at", DateTime, nullable=False),
    Column("received_at", DateTime, nullable=False),
    Column("value", Float), Column("unit", String(30), nullable=False),
    Column("source", String(80), nullable=False),
    Column("record_key", String(180), nullable=False),
    Column("verification", String(40), nullable=False),
    UniqueConstraint("source", "record_key", "sensor", "metric", name="uq_source_record"),
    Index("ix_station_time", "station", "observed_at"),
    Index("ix_station_source", "station", "source"),
    Index("ix_signal_time", "station", "sensor", "metric", "observed_at"),
)
sources = Table(
    "source_state", metadata, Column("id", String(80), primary_key=True),
    Column("cursor", Integer), Column("last_success", DateTime),
    Column("error", String(200)), Column("detail", Text),
)
jobs = Table(
    "export_jobs", metadata, Column("id", String(40), primary_key=True),
    Column("station", String(80), nullable=False),
    Column("status", String(20), nullable=False),
    Column("created_at", DateTime, nullable=False), Column("expires_at", DateTime, nullable=False),
    Column("error", String(200)), Column("filename", String(180)),
)
imports = Table(
    "import_batches", metadata, Column("checksum", String(64), primary_key=True),
    Column("filename", String(180)), Column("finished_at", DateTime),
    Column("inserted", Integer), Column("skipped", Integer), Column("details", Text),
)

def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)

WIB = timezone(timedelta(hours=7))

def timestamp(value):
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=WIB)
    return dt.astimezone(timezone.utc).replace(tzinfo=None)

def iso(value):
    return value.replace(tzinfo=timezone.utc).astimezone(WIB).isoformat() if value else None

def engine(url=None):
    return create_engine(url or os.environ["ARCHIVE_DATABASE_URL"], pool_pre_ping=True)

def initialize(db):
    # Only the dedicated archive schema is touched. Existing Laravel tables are not migrated.
    metadata.create_all(db)
    with db.connect() as conn:
        conn.execute(text("SELECT 1"))

def upsert(db, table, rows, key_columns=()):
    if not rows:
        return
    with db.begin() as conn:
        if db.dialect.name == "mysql":
            stmt = mysql_insert(table).values(rows)
            stmt = stmt.on_duplicate_key_update(**{
                c.name: stmt.inserted[c.name] for c in table.c if not c.primary_key and c.name in rows[0]
            })
        elif db.dialect.name == "sqlite":
            stmt = sqlite_insert(table).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=list(key_columns),
                set_={c.name: getattr(stmt.excluded, c.name) for c in table.c if not c.primary_key and c.name in rows[0]},
            )
        else:
            raise RuntimeError("Unsupported archive driver")
        conn.execute(stmt)

def save_readings(db, rows):
    for offset in range(0, len(rows), 100):
        upsert(db, observations, rows[offset:offset + 100], ("source", "record_key", "sensor", "metric"))

def set_source(db, source_id, **values):
    with db.begin() as conn:
        existing = conn.execute(select(sources).where(sources.c.id == source_id)).mappings().first()
        data = dict(existing) if existing else {"id": source_id}
        data.update(values)
    upsert(db, sources, [data], ("id",))

def read_source(db, source_id):
    with db.connect() as conn:
        row = conn.execute(select(sources).where(sources.c.id == source_id)).mappings().first()
        return dict(row) if row else {}
