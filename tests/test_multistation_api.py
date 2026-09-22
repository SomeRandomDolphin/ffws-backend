"""Tes endpoint API hybrid multi-stasiun tanpa artefak lokal."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from api.main import app
from dhompo.data.loader import ALL_STATIONS


def _payload(periods: int = 48) -> list[dict]:
    start = datetime(2023, 1, 1)
    return [
        {
            "timestamp": (start + timedelta(minutes=30 * step)).isoformat(),
            "readings": {
                station: float(station_idx + step / 100.0)
                for station_idx, station in enumerate(ALL_STATIONS)
            },
        }
        for step in range(periods)
    ]


@dataclass
class _Result:
    predictions: dict
    simulator_predictions: dict
    future_rainfall_mode: str
    scenario_spread: dict | None = None
    scenario_count: int = 1


class _FakeHybridPredictor:
    backend_name = "hybrid_multistation"

    def model_mapping(self):
        return {f"h{h}": f"fake_h{h}" for h in range(1, 7)}

    def _values(self):
        return {
            f"h{h}": {station: float(h) for station in ALL_STATIONS}
            for h in range(1, 7)
        }

    def predict_from_history(self, history, future_rainfall=None):
        values = self._values()
        return _Result(values, values, "provided")

    def predict_ensemble_from_history(self, history, scenario_count=20, seed=42):
        values = self._values()
        scenario_spread = {
            horizon: {
                station: {"p10": value - 0.1, "p50": value, "p90": value + 0.1}
                for station, value in stations.items()
            }
            for horizon, stations in values.items()
        }
        return _Result(
            values,
            values,
            "markov_gamma_ensemble",
            scenario_spread=scenario_spread,
            scenario_count=scenario_count,
        )


def test_multistation_api_ensemble_response():
    with TestClient(app) as client:
        app.state.hybrid_predictor = _FakeHybridPredictor()
        response = client.post(
            "/predict-multistation",
            json={"history": _payload(), "scenario_count": 8, "seed": 7},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["backend"] == "hybrid_multistation"
    assert data["scenario_count"] == 8
    assert data["future_rainfall_mode"] == "markov_gamma_ensemble"
    assert len(data["predictions"]) == 6
    assert len(data["predictions"]["h6"]) == 15
    assert data["scenario_spread"]["h6"]["Dhompo"] == {
        "p10": 5.9, "p50": 6.0, "p90": 6.1,
    }
    assert data["operationally_validated"] is False
    assert data["uncertainty_calibrated"] is False


def test_multistation_api_provided_rainfall_response():
    with TestClient(app) as client:
        app.state.hybrid_predictor = _FakeHybridPredictor()
        response = client.post(
            "/predict-multistation",
            json={"history": _payload(), "future_rainfall": [1.0] * 12},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["scenario_count"] == 1
    assert data["scenario_spread"] is None
    assert data["future_rainfall_mode"] == "provided"


def test_multistation_api_rejects_missing_station():
    history = _payload()
    del history[-1]["readings"]["Bd. Sentono"]
    with TestClient(app) as client:
        response = client.post("/predict-multistation", json={"history": history})
    assert response.status_code == 422


def test_multistation_api_rejects_wrong_rainfall_length():
    with TestClient(app) as client:
        response = client.post(
            "/predict-multistation",
            json={"history": _payload(), "future_rainfall": [1.0] * 6},
        )
    assert response.status_code == 422
