"""Tes predictor multi-stasiun hybrid dengan artefak sementara."""

from __future__ import annotations

import json

import joblib
import numpy as np
import pandas as pd
import pytest
import yaml
from sklearn.dummy import DummyRegressor

from dhompo.data.features import build_multistation_features
from dhompo.data.loader import ALL_STATIONS, DataSegment
from dhompo.data.network import load_network
from dhompo.data.routing_sim import fit_routing_parameters
from dhompo.serving.hybrid_predictor import HybridMultiStationPredictor


def _history(periods: int = 72) -> pd.DataFrame:
    index = pd.date_range("2022-01-01", periods=periods, freq="30min", name="Datetime")
    time = np.arange(periods, dtype=float)
    return pd.DataFrame(
        {
            station: 10.0 + station_idx * 5.0 + np.sin(time / 8.0)
            for station_idx, station in enumerate(ALL_STATIONS)
        },
        index=index,
    )


@pytest.fixture
def predictor(tmp_path) -> HybridMultiStationPredictor:
    history = _history()
    network = load_network()
    routing = fit_routing_parameters(
        [DataSegment(history, "reference", None)], network, {"routing": {}},
    )
    features = build_multistation_features(history)
    simulator_columns = [f"simulator_{station}" for station in ALL_STATIONS]
    training_features = pd.concat([
        features,
        pd.DataFrame(0.0, index=features.index, columns=simulator_columns),
    ], axis=1)
    target = np.zeros((len(training_features), len(ALL_STATIONS)))
    for horizon in range(1, 7):
        model = DummyRegressor(strategy="constant", constant=np.zeros(len(ALL_STATIONS)))
        model.fit(training_features, target)
        joblib.dump(model, tmp_path / f"residual_h{horizon}.joblib")
    joblib.dump(routing, tmp_path / "routing_parameters.joblib")
    metadata = {
        "stations": ALL_STATIONS,
        "horizons": list(range(1, 7)),
        "history_rows": 48,
        "base_features": list(features.columns),
    }
    (tmp_path / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    simulator_path = tmp_path / "simulator.yaml"
    simulator_path.write_text(
        yaml.safe_dump({
            "rainfall": {
                "p_wet_after_dry": 0.2,
                "p_wet_after_wet": 0.7,
                "gamma_shape": 1.5,
                "gamma_scale": 2.0,
                "monthly_multipliers": [1.0] * 12,
                "max_rain_mm": 20.0,
            },
            "routing": routing.to_dict(),
        }),
        encoding="utf-8",
    )
    return HybridMultiStationPredictor(tmp_path, simulator_path)


def test_predictor_returns_all_stations_and_horizons(predictor):
    result = predictor.predict_from_history(_history())
    assert set(result.predictions) == {f"h{h}" for h in range(1, 7)}
    assert set(result.predictions["h6"]) == set(ALL_STATIONS)
    assert set(result.simulator_predictions["h6"]) == set(ALL_STATIONS)
    assert result.future_rainfall_mode == "zero"


def test_predictor_accepts_future_rainfall(predictor):
    rainfall = pd.Series([2.0] * 12)
    result = predictor.predict_from_history(_history(), future_rainfall=rainfall)
    assert result.future_rainfall_mode == "provided"
    assert result.predictions["h6"]["Dhompo"] > 0.0


def test_predictor_rejects_short_history(predictor):
    with pytest.raises(ValueError, match="at least 48"):
        predictor.predict_from_history(_history(47))


def test_predictor_rejects_short_future_rainfall(predictor):
    with pytest.raises(ValueError, match="12 langkah"):
        predictor.predict_from_history(_history(), future_rainfall=pd.Series([1.0] * 3))


def test_ensemble_is_reproducible_and_intervals_are_ordered(predictor):
    first = predictor.predict_ensemble_from_history(_history(), scenario_count=8, seed=9)
    second = predictor.predict_ensemble_from_history(_history(), scenario_count=8, seed=9)
    assert first == second
    assert first.scenario_count == 8
    assert first.future_rainfall_mode == "markov_gamma_ensemble"
    for horizon in first.scenario_spread.values():
        for interval in horizon.values():
            assert interval["p10"] <= interval["p50"] <= interval["p90"]


def test_ensemble_members_have_expected_shape(predictor):
    hybrid, simulator = predictor.ensemble_members_from_history(
        _history(), scenario_count=5, seed=2,
    )
    assert hybrid[6].shape == (5, len(ALL_STATIONS))
    assert simulator[1].shape == (5, len(ALL_STATIONS))
