"""Explicit bootstrap and immutable forecast migration; never copies raw Surabaya archive."""
import argparse
from pathlib import Path
from sqlalchemy import select,text,func
from sqlalchemy.dialects.postgresql import insert
from monitoring.store import engine,initialize,cloud_mode
from monitoring import cloud,shadow
from monitoring.collect import import_excel

def main():
 p=argparse.ArgumentParser();p.add_argument('command',choices=['bootstrap','verify','migrate-predictions']);p.add_argument('--sqlite');a=p.parse_args()
 if not cloud_mode(): raise RuntimeError('Set MONITORING_STORAGE=supabase_compact explicitly')
 db=engine(); initialize(db)
 if a.command=='bootstrap':
  root=Path(__file__).resolve().parents[1]
  for path in sorted((root/'imports/history').glob('*.csv')): print(path.name,import_excel(db,path),flush=True)
  cloud.sync(db)
 elif a.command=='migrate-predictions':
  if not a.sqlite: p.error('--sqlite is required')
  source=engine('sqlite:///'+Path(a.sqlite).resolve().as_posix())
  for table in [shadow.runs,shadow.forecasts,shadow.preview_history]:
   with source.connect() as c: rows=[dict(row) for row in c.execute(select(table)).mappings()]
   if rows:
    with db.begin() as c: c.execute(insert(table).values(rows).on_conflict_do_nothing(index_elements=['id']))
    with db.connect() as c:
     actual={row['id']:dict(row) for row in c.execute(select(table).where(table.c.id.in_([r['id'] for r in rows]))).mappings()}
    import math
    assert all(all(math.isclose(value,actual[row['id']][key],rel_tol=1e-12,abs_tol=1e-9) if isinstance(value,float) else value==actual[row['id']][key] for key,value in row.items()) for row in rows), 'Migrated forecast mismatch'
   print(table.name,len(rows),'verified',flush=True)
 with db.connect() as c:
  print('Size:',c.execute(text('select pg_size_pretty(pg_database_size(current_database()))')).scalar(),flush=True)
  for table in [cloud.observations,cloud.stats,shadow.runs,shadow.forecasts,shadow.preview_history]:
   print(table.name,c.execute(select(func.count()).select_from(table)).scalar(),flush=True)
 db.dispose()

if __name__=='__main__': main()
