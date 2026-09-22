"""Tests for two-tier predictor routing"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from dhompo.data.clusters import UPSTREAM_TELEMETRY
from dhompo.data.loader import TARGET_STATION
from dhompo.etl.quality import QualityFlag
from dhompo.serving.file_predictor import PredictionResult
from dhompo.serving.two_tier import (
    PersistenceTierB,
    RoutedPrediction,
    TwoTierPredictor,
)
from tests.conftest import make_history_df


class FakeTierA:
    backend_name = "file"

    def model_mapping(self) -> dict[str, str]:
        return {f"h{h}": f"fake_h{h}.pkl" for h in range(1, 6)}

    def predict_from_history(self, history: pd.DataFrame) -> PredictionResult:
        return PredictionResult(
            predictions={f"h{h}": 1.0 + 0.1 * h for h in range(1, 6)},
            model_version="fake_a_v1",
            confidence="high",
        )


@pytest.fixture
def healthy_history() -> pd.DataFrame:
    return make_history_df(n_rows=48)


def test_route_serves_tier_a_when_telemetry_healthy(healthy_history):
    predictor = TwoTierPredictor(tier_a=FakeTierA(), tier_b=PersistenceTierB())
    routed = predictor.route(healthy_history)
    assert routed.serving_tier == "A"
    assert routed.shadow_predictions is not None  # Tier-B shadowed
    assert set(routed.predictions.keys()) == {"h1", "h2", "h3", "h4", "h5"}
    assert routed.predictions["h3"] == pytest.approx(1.3)


def test_route_falls_back_to_tier_b_when_all_telemetry_down(healthy_history):
    df = healthy_history.copy()
    df.loc[df.index[-6:], list(UPSTREAM_TELEMETRY)] = 0.0  # zero-shortcut FLATLINE
    predictor = TwoTierPredictor(tier_a=FakeTierA(), tier_b=PersistenceTierB())
    routed = predictor.route(df)
    assert routed.serving_tier == "B"
    assert routed.shadow_predictions is None  # no shadow when B serves
    last_dhompo = round(float(df[TARGET_STATION].dropna().iloc[-1]), 4)
    for h in range(1, 6):
        assert routed.predictions[f"h{h}"] == pytest.approx(last_dhompo)


def test_route_stays_on_tier_a_when_only_one_telemetry_down(healthy_history):
    df = healthy_history.copy()
    df.loc[df.index[-6:], "Klosod"] = 0.0  # only Klosod fails
    predictor = TwoTierPredictor(tier_a=FakeTierA(), tier_b=PersistenceTierB())
    routed = predictor.route(df)
    assert routed.serving_tier == "A"


def test_degradation_populated_when_primary_station_bad(healthy_history):
    df = healthy_history.copy()
    df.loc[df.index[-6:], "Purwodadi"] = 0.0  # primary for h4, h5
    predictor = TwoTierPredictor(tier_a=FakeTierA(), tier_b=PersistenceTierB())
    routed = predictor.route(df)
    assert "h4" in routed.degradation
    assert "h5" in routed.degradation
    assert "Purwodadi" in routed.degradation["h4"]
    # h1's primary (Klosod) is still healthy → no degradation
    assert "h1" not in routed.degradation


def test_persistence_tier_b_predicts_last_dhompo(healthy_history):
    tier_b = PersistenceTierB()
    last = round(float(healthy_history[TARGET_STATION].dropna().iloc[-1]), 4)
    result = tier_b.predict_from_history(healthy_history)
    for h in range(1, 6):
        assert result.predictions[f"h{h}"] == pytest.approx(last)


def test_two_tier_backend_name_proxies_tier_a():
    predictor = TwoTierPredictor(tier_a=FakeTierA(), tier_b=PersistenceTierB())
    assert predictor.backend_name == "file"


def test_quality_flags_returned_in_routed_response(healthy_history):
    predictor = TwoTierPredictor(tier_a=FakeTierA(), tier_b=PersistenceTierB())
    routed = predictor.route(healthy_history)
    assert isinstance(routed.quality_flags, dict)
    assert TARGET_STATION in routed.quality_flags
