"""Integration tests for the Dhompo Flood Prediction API."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.main import app
from tests.conftest import make_history_payload


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


class TestHealth:
    def test_health(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["ready"] is True
        assert data["backend"] == "file"
        assert data["error"] is None
        assert "timestamp" in data

    def test_health_not_ready(self, monkeypatch):
        monkeypatch.setattr(
            "api.predictor_state.create_predictor",
            lambda: (_ for _ in ()).throw(RuntimeError("model bootstrap failed")),
        )
        with TestClient(app) as failing_client:
            resp = failing_client.get("/health")

        assert resp.status_code == 503
        data = resp.json()
        assert data["status"] == "error"
        assert data["ready"] is False
        assert data["error"] == "model bootstrap failed"

    def test_model_info(self, client):
        resp = client.get("/model-info")
        assert resp.status_code == 200
        data = resp.json()
        assert data["backend"] == "file"
        assert data["ready"] is True
        assert data["error"] is None
        assert "mlflow" in data["available_backends"]
        assert "h1" in data["models"]
        assert "h5" in data["models"]
        assert data["required_history_rows"] == 24
        assert "Dhompo" in data["required_stations"]

    def test_model_info_mlflow_backend(self, monkeypatch):
        monkeypatch.setenv("PREDICTOR_BACKEND", "mlflow")
        with TestClient(app) as mlflow_client:
            resp = mlflow_client.get("/model-info")
            assert resp.status_code == 200
            data = resp.json()

        assert data["backend"] == "mlflow"
        assert data["models"]["h1"] == "models:/dhompo_h1@production"
        monkeypatch.delenv("PREDICTOR_BACKEND", raising=False)


class TestPredict:
    def test_predict_success(self, client, sample_history_payload):
        resp = client.post("/predict", json={"history": sample_history_payload})
        assert resp.status_code == 200
        data = resp.json()
        preds = data["predictions"]
        for key in ("h1", "h2", "h3", "h4", "h5"):
            assert key in preds
            assert isinstance(preds[key], float)
        assert data["backend"] == "file"
        assert "h1" in data["models"]
        assert "timestamp" in data
        assert "prediction_time" in data

    def test_predict_not_ready(self, monkeypatch, sample_history_payload):
        monkeypatch.setattr(
            "api.predictor_state.create_predictor",
            lambda: (_ for _ in ()).throw(RuntimeError("model bootstrap failed")),
        )
        with TestClient(app) as failing_client:
            resp = failing_client.post("/predict", json={"history": sample_history_payload})

        assert resp.status_code == 503
        assert resp.json()["detail"] == "Predictor is not ready: model bootstrap failed"

    def test_predict_insufficient_history(self, client):
        payload = make_history_payload(n_rows=10)
        resp = client.post("/predict", json={"history": payload})
        assert resp.status_code == 422

    def test_predict_missing_stations(self, client):
        """Send rows with only Dhompo — missing upstream stations."""
        from datetime import datetime, timedelta

        rows = []
        start = datetime(2022, 11, 21, 0, 0)
        for i in range(24):
            rows.append({
                "timestamp": (start + timedelta(minutes=30 * i)).isoformat(),
                "readings": {"Dhompo": 1.0},
            })
        resp = client.post("/predict", json={"history": rows})
        assert resp.status_code == 422

    def test_predict_response_shape(self, client, sample_history_payload):
        resp = client.post("/predict", json={"history": sample_history_payload})
        assert resp.status_code == 200
        data = resp.json()
        # Verify all expected top-level keys
        assert set(data.keys()) == {
            "predictions", "backend", "models", "timestamp", "prediction_time",
            "serving_tier", "degradation", "shadow_predictions", "quality_flags",
        }
        # Verify predictions has exactly h1..h5
        assert set(data["predictions"].keys()) == {"h1", "h2", "h3", "h4", "h5"}

    def test_predict_empty_body(self, client):
        resp = client.post("/predict", json={})
        assert resp.status_code == 422

    def test_predict_bad_timestamp_boundary(self, client):
        """Timestamps not on 30-min boundary should fail validation."""
        from datetime import datetime, timedelta

        rows = []
        start = datetime(2022, 11, 21, 0, 15)  # :15 — not a boundary
        for i in range(24):
            rows.append({
                "timestamp": (start + timedelta(minutes=30 * i)).isoformat(),
                "readings": {"Dhompo": 1.0},
            })
        resp = client.post("/predict", json={"history": rows})
        assert resp.status_code == 422
