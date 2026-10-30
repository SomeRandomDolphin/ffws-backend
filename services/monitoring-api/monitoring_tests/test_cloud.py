from datetime import datetime,timedelta
from monitoring.cloud import aggregate_records

def test_aggregation_preserves_raw_scale_counts_and_excludes_open_bin():
 t=datetime(2026,1,1)
 def row(i,v): return dict(id=i,station='lokasi_1_hang_tuah',sensor='distance',metric='water_level',observed_at=t+timedelta(seconds=30*i),received_at=t,value=v,unit='cm',source='mysql:test',verification='unverified')
 rows=[row(i,100+i) for i in range(10)] + [row(10,900)]
 means,stats=aggregate_records(rows,'mysql:test',t+timedelta(minutes=5))
 assert len(means)==len(stats)==1
 assert means[0]['value']==104.5
 assert stats[0]['minimum']==100 and stats[0]['maximum']==109
 assert stats[0]['samples']==10 and stats[0]['valid_samples']==10
 assert means[0]['observed_at']==t+timedelta(minutes=5)
 # Replaying the same bucket produces the same natural observation key.
 assert aggregate_records(rows,'mysql:test',t+timedelta(minutes=5))[0][0]['record_key']==means[0]['record_key']

def test_conflicting_duplicates_and_zero_are_visible_in_quality():
 t=datetime(2026,1,1)
 base=dict(station='lokasi_1_hang_tuah',sensor='distance',metric='water_level',received_at=t,unit='cm',verification='unverified')
 rows=[dict(base,id=1,observed_at=t,value=100),dict(base,id=2,observed_at=t,value=101),dict(base,id=3,observed_at=t+timedelta(seconds=30),value=0),dict(base,id=4,observed_at=t+timedelta(seconds=60),value=600)]
 means,stats=aggregate_records(rows,'mysql:test',t+timedelta(minutes=5))
 assert stats[0]['samples']==3 and stats[0]['valid_samples']==0
 assert stats[0]['zero_samples']==1 and stats[0]['invalid_samples']==3
 assert means[0]['value']==300  # mean raw remains auditable, never mislabeled as raw data


def test_batch_snapshots_keep_source_priority_and_missing_stations():
 from monitoring.store import engine,initialize,save_readings,now,set_source
 from monitoring.queries import snapshot,snapshots_batch
 db=engine('sqlite://');initialize(db);stamp=now()
 base=dict(station='welang-dhompo',sensor='water_level',metric='water_level',observed_at=stamp,received_at=stamp,unit='m',verification='test')
 save_readings(db,[dict(base,value=1,source='excel:welang',record_key='a'),dict(base,value=2,source='api:welang',record_key='b')])
 set_source(db,'api:welang',last_success=stamp,error=None)
 expected=snapshot(db,'welang-dhompo')
 actual=snapshots_batch(db,['welang-dhompo','lokasi_1_hang_tuah'])
 assert actual[0]['state']==expected['state']
 assert actual[0]['series'][0]['points']==expected['series'][0]['points']
 assert actual[0]['series'][0]['points'][0]['value']==2
 assert actual[1]['state']=='unavailable'
 db.dispose()


def test_cloud_median_equals_model_and_preserves_extrema():
 from monitoring.shadow import bins
 t=datetime(2026,1,1)
 rows=[dict(id=i,station='lokasi_1_hang_tuah',sensor='distance',metric='water_level',observed_at=t+timedelta(seconds=30*i),received_at=t,value=v,unit='cm',source='test',verification='test') for i,v in enumerate([2,2,2,2,40])]
 end=t+timedelta(minutes=5)
 means,details=aggregate_records(rows,'test',end)
 assert means[0]['value']==details[0]['median_distance']==2
 assert 500-details[0]['median_distance']==bins(rows,500,end)[end]
 assert details[0]['minimum']==2 and details[0]['maximum']==40
 rows[0]['value']=0
 assert aggregate_records(rows,'test',end)[1][0]['median_distance'] is None

def test_cloud_frozen_bin_matches_causal_model():
 from monitoring.shadow import bins
 t=datetime(2026,1,1);end=t+timedelta(hours=6,minutes=10)
 rows=[dict(id=i,station='lokasi_1_hang_tuah',sensor='distance',metric='water_level',observed_at=t+timedelta(seconds=30*i),received_at=t,value=100,unit='cm',source='test',verification='test') for i in range(740)]
 _,details=aggregate_records(rows,'test',end)
 lookup={r['bucket']:r for r in details}
 assert lookup[t+timedelta(minutes=5)]['median_distance']==100
 assert lookup[end]['median_distance'] is None
 assert bins(rows,500,end)[end] is None


def test_median_series_does_not_create_artificial_gaps_by_downsampling():
 from monitoring.queries import format_series
 t=datetime(2026,1,1)
 rows=[dict(id=i,station='lokasi_1_hang_tuah',sensor='distance',metric='water_level',observed_at=t+timedelta(minutes=5*i),received_at=t,value=100+i,unit='cm',source='test',record_key='5m:'+str(i),verification='test') for i in range(4)]
 series=format_series(rows,'lokasi_1_hang_tuah',max_points=2)[0]
 assert series['aggregationMethod']=='median_valid_5min'
 assert len(series['points'])==4
