import math
from datetime import datetime,timedelta
from unittest.mock import patch
from sqlalchemy import select
from monitoring import shadow
from monitoring.store import engine,initialize,observations

def test_causal_quality_and_models():
 t=datetime(2026,1,1)
 rows=[{"observed_at":t+timedelta(seconds=30*i),"value":100.} for i in range(740)]
 end=t+timedelta(hours=6,minutes=10)
 grid=shadow.bins(rows,500,end)
 assert grid[t+timedelta(minutes=5)]==400
 assert grid[end] is None
 prefix=shadow.bins(rows[:600],500,t+timedelta(hours=5))
 assert all(grid[k]==v for k,v in prefix.items())
 rows[0]["value"]=0
 assert shadow.bins(rows,500,end)[t+timedelta(minutes=5)] is None
 for slope in [0,10,-10]:
  values=[200+slope*i/12 for i in range(72)]
  pred=shadow.make_predictions(values,530)
  assert pred['persistence']==[values[-1]]*5
  assert abs(pred['damped_trend'][0]-(values[-1]+slope*(1-math.exp(-1))))<1e-8

def test_issue_score_idempotency_and_missing_targets():
 db=engine('sqlite://'); initialize(db)
 origin=datetime(2026,1,2,12)
 config={"locations":[{"key":"test","water_level":{"reference_cm":500,"columns":["distance"]}}]}
 rows=[]
 for i in range(1440):
  stamp=origin-timedelta(hours=12)+timedelta(seconds=30*i)
  rows.append(dict(station='test',sensor='distance',metric='distance',observed_at=stamp,received_at=stamp,value=200+i%10,unit='cm',source='test',record_key=str(i),verification='test'))
 with db.begin() as c: c.execute(observations.insert(),rows)
 with patch.object(shadow,'live_config',return_value=config):
  shadow.cycle(db,origin+timedelta(minutes=1)); shadow.cycle(db,origin+timedelta(minutes=2))
  with db.connect() as c:
   issued=c.execute(select(shadow.forecasts)).mappings().all()
   assert len(issued)==10
  target=origin+timedelta(hours=1)
  actual=[]
  for i in range(10):
   stamp=target-timedelta(minutes=5)+timedelta(seconds=30*i)
   actual.append(dict(station='test',sensor='distance',metric='distance',observed_at=stamp,received_at=stamp,value=220+i%2,unit='cm',source='test',record_key='a'+str(i),verification='test'))
  with db.begin() as c: c.execute(observations.insert(),actual)
  shadow.cycle(db,target+timedelta(minutes=11))
  with db.connect() as c:
   scored=c.execute(select(shadow.forecasts).where(shadow.forecasts.c.status=='scored')).mappings().all()
   assert len(scored)==2
   assert all(r['actual_cm']==279.5 for r in scored)
  shadow.cycle(db,origin+timedelta(hours=30,minutes=11))
  with db.connect() as c:
   final=c.execute(select(shadow.forecasts)).mappings().all()
   assert sum(r['status']=='unavailable' for r in final)==8
   assert [r['predicted_cm'] for r in final]==[r['predicted_cm'] for r in issued]
 db.dispose()


def test_preview_history_immutable_and_date_range_export():
    from io import BytesIO
    import xlsxwriter
    import openpyxl
    db=engine('sqlite://'); initialize(db)
    origin=datetime(2026,1,2,12); values=[200+i%2 for i in range(72)]
    predictions=shadow.make_predictions(values,500)
    first=shadow.save_preview(db,'test','distance',origin,origin+timedelta(days=1),500,values,predictions)
    assert shadow.save_preview(db,'test','distance',origin,origin+timedelta(days=2),500,values,predictions)==first
    with db.connect() as c:
        saved=c.execute(select(shadow.preview_history)).mappings().all()
        assert len(saved)==10
        assert all(r['computed_at']==origin+timedelta(days=1) for r in saved)
        assert not c.execute(select(shadow.forecasts)).first()
    output=BytesIO()
    with xlsxwriter.Workbook(output) as book:
        fmt=book.add_format({'num_format':'yyyy-mm-dd hh:mm:ss'})
        shadow.export_prediction_sheet(book,db,'test',origin+timedelta(hours=2),origin+timedelta(hours=3),fmt)
    output.seek(0); workbook=openpyxl.load_workbook(output)
    rows=list(workbook['Prediksi'].values)
    assert len(rows)==5  # header plus two models at two target times
    assert {r[5] for r in rows[1:]}=={2,3}
    assert all(r[8]=='Replay data terakhir' for r in rows[1:])
    assert rows[1][1].hour==21  # UTC 14:00 is WIB 21:00
    db.dispose()
