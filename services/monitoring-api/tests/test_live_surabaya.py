from datetime import datetime, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.surabaya import router
from dhompo.config import load_yaml_config
from dhompo.serving.live_surabaya import LiveSurabaya, WIB, identifier


@pytest.fixture
def service(tmp_path):
    return LiveSurabaya(load_yaml_config("configs/surabaya/live_sources.yaml"), tmp_path / "live.sqlite3")


def seed(service, index, now, values):
    loc = service.config["locations"][index]
    spec = loc["water_level"]
    key = loc["database"] + "." + spec["table"]
    rows = [{spec["id_column"]: i + 1,
             spec["timestamp_column"]: now - timedelta(minutes=30 * (len(values) - i - 1)),
             **value} for i, value in enumerate(values)]
    service.ingest(key, spec, rows)
    service.last_success[key] = now.isoformat()
    return loc, key, spec


def test_independent_sensors_zero_and_missing_rain(service):
    now = datetime.now(WIB)
    seed(service, 1, now, [{"distance1": 0, "distance2": 508.9}])
    result = service.build_snapshot(now)
    kalibokor = result["stations"][1]
    assert [s["valueCm"] for s in kalibokor["sensors"]] == [0, 508.9]
    assert not kalibokor["sensors"][0]["forecast"]["points"]
    assert result["stations"][3]["rainfall"] is None


def test_idempotent_ingest_survives_restart(service):
    now = datetime.now(WIB)
    _, key, spec = seed(service, 3, now, [{"distance1": 249}])
    service.ingest(key, spec, [{"id": 1, "waktu": now, "distance1": 250}])
    assert len(service.read_rows(key)) == 1
    restarted = LiveSurabaya(service.config, service.cache_path)
    station = restarted.get_snapshot()["stations"][3]
    assert station["sensors"][0]["valueCm"] == 250
    assert station["state"] == "cached"
    assert not station["sensors"][0]["forecast"]["points"]


def test_forecast_is_per_sensor_and_uses_observation_time(service):
    now = datetime.now(WIB)
    seed(service, 2, now, [{"distance1": 342, "distance2": 416.4}])
    station = service.build_snapshot(now)["stations"][2]
    a, b = [s["forecast"] for s in station["sensors"]]
    assert a["method"] == "persistence"
    assert [p["valueCm"] for p in a["points"]] == [342] * 5
    assert [p["valueCm"] for p in b["points"]] == [416.4] * 5
    assert datetime.fromisoformat(a["points"][0]["time"]) == now + timedelta(hours=1)
    assert station["sensors"][0]["waterLevelCm"] == 188
    assert a["modelVersion"] == "persistence_v1"
    assert a["usesPumpTelemetry"] is False


@pytest.mark.parametrize("minutes,state", [(11, "stale"), (-2, "future")])
def test_old_or_future_data_never_forecast(service, minutes, state):
    now = datetime.now(WIB)
    seed(service, 3, now - timedelta(minutes=minutes), [{"distance1": 249}])
    station = service.build_snapshot(now)["stations"][3]
    assert station["state"] == state
    assert not station["sensors"][0]["forecast"]["points"]


def test_hang_tuah_model_inputs_are_causal_and_missing_flags_explicit(service):
    now = datetime.now(WIB).replace(minute=0, second=0, microsecond=0)
    target = "ketinggian_lokasi_1_hang_tuah"
    other = "ketinggian_lokasi_2_kalibokor"
    def predict(values, flags):
        assert len(values) == 24
        assert values.index[-1] == pd.Timestamp(now).tz_localize(None)
        assert values[target].iloc[-1] == 323
        assert values[other].isna().all()
        assert (flags[other] == "MISSING").all()
        return SimpleNamespace(predictions={"h1": 324.0})
    service.predictor = SimpleNamespace(
        target_column=target, source_signals=[target, other], predict_from_history=predict
    )
    seed(service, 0, now, [{"distance": 300 + i} for i in range(24)])
    result = service.build_snapshot(now)["stations"][0]["sensors"][0]["forecast"]
    assert result["method"] == "urban_file"
    assert result["points"][0]["valueCm"] == 324


def test_failed_sync_returns_cached_values_without_secret(service, monkeypatch):
    now = datetime.now(WIB)
    seed(service, 3, now, [{"distance1": 249}])
    def fail(**kwargs):
        raise RuntimeError("SECRET password host")
    service.connect = fail
    monkeypatch.setattr("dhompo.serving.live_surabaya.load_yaml_config", lambda _: {
        "host": "private", "username": "user", "password": "SECRET",
    })
    service.sync_once()
    data = service.get_snapshot()
    assert "SECRET" not in str(data)
    assert data["stations"][3]["state"] == "cached"
    assert not data["stations"][3]["sensors"][0]["forecast"]["points"]


def test_snapshot_route_and_unready_state(service):
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    assert client.get("/surabaya/snapshot").status_code == 503
    app.state.surabaya = service
    response = client.get("/surabaya/snapshot")
    assert response.status_code == 200
    assert len(response.json()["stations"]) == 4


def test_identifier_rejects_untrusted_sql():
    with pytest.raises(ValueError):
        identifier("esp1; DROP TABLE esp1")
