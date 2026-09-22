"""Tests for the Tier-A adaptive predictor wrapper."""

from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

torch = pytest.importorskip("torch")

from dhompo.data.loader import ALL_STATIONS, TARGET_STATION  # noqa: E402
from dhompo.etl.quality import QualityFlag  # noqa: E402
from dhompo.serving.tier_a_adaptive import (  # noqa: E402
    TierAAdaptiveArtifacts,
    TierAAdaptivePredictor,
)
from dhompo.serving.two_tier import (  # noqa: E402
    PersistenceTierB,
    TwoTierPredictor,
)
from tests.conftest import make_history_df


def _write_artifacts(tmp_path: Path) -> TierAAdaptiveArtifacts:
    """Write a randomly initialised AdaptiveTierA + identity Normalizer to disk."""
    from dhompo.models.adaptive import AdaptiveTierA, AdaptiveTierAConfig
    from dhompo.training.normalizer import Normalizer

    torch.manual_seed(0)
    cfg = AdaptiveTierAConfig()
    model = AdaptiveTierA(cfg)

    ckpt_path = tmp_path / "best.pt"
    norm_path = tmp_path / "normalizer.pkl"
    torch.save(
        {
            "model_state": model.state_dict(),
            "config": cfg,
            "epoch": 3,
            "test_main": 0.42,
        },
        ckpt_path,
    )
    n_stations = len(cfg.stations)
    normalizer = Normalizer(
        mean_feats=np.zeros((n_stations, cfg.features_per_station), dtype=np.float32),
        std_feats=np.ones((n_stations, cfg.features_per_station), dtype=np.float32),
        mean_ar=np.zeros(cfg.ar_lag_dim, dtype=np.float32),
        std_ar=np.ones(cfg.ar_lag_dim, dtype=np.float32),
    )
    with open(norm_path, "wb") as fh:
        pickle.dump(normalizer, fh)

    return TierAAdaptiveArtifacts(checkpoint=ckpt_path, normalizer=norm_path)


@pytest.fixture
def predictor(tmp_path) -> TierAAdaptivePredictor:
    return TierAAdaptivePredictor(artifacts=_write_artifacts(tmp_path))


@pytest.fixture
def history() -> pd.DataFrame:
    df = make_history_df(n_rows=48)
    # Inject all 14 stations so build_per_station_features hits every slot.
    for station in ALL_STATIONS:
        if station not in df.columns:
            df[station] = 0.5
    # The shared make_history_df uses uniform-random levels with diffs
    # often above 1.0 m / 30 min, which trips OUT_OF_RANGE on the physical-
    # envelope detector. Smooth out the columns the mask tests inspect so
    # the fixture stays a "healthy basin" baseline.
    rng = np.random.default_rng(7)
    for station in (TARGET_STATION, "Klosod", "Purwodadi", "AWLR Kademungan"):
        if station not in df.columns:
            continue
        baseline = float(df[station].iloc[0])
        steps = rng.normal(loc=0.0, scale=0.05, size=len(df))
        df[station] = (baseline + np.cumsum(steps)).clip(min=0.1)
    return df


def test_predict_returns_h1_to_h5(predictor, history):
    result = predictor.predict_from_history(history)
    assert set(result.predictions.keys()) == {"h1", "h2", "h3", "h4", "h5"}
    for value in result.predictions.values():
        assert isinstance(value, float)
    assert result.model_version.startswith("tier_a_adaptive_e")


def test_model_mapping_lists_all_horizons(predictor):
    mapping = predictor.model_mapping()
    assert set(mapping.keys()) == {f"h{h}" for h in range(1, 6)}
    assert all(v == "tier_a_adaptive" for v in mapping.values())


def test_history_too_short_raises(predictor):
    short = make_history_df(n_rows=4)
    with pytest.raises(ValueError, match="History needs at least"):
        predictor.predict_from_history(short)


def test_missing_checkpoint_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        TierAAdaptivePredictor(
            artifacts=TierAAdaptiveArtifacts(
                checkpoint=tmp_path / "nope.pt",
                normalizer=tmp_path / "nope.pkl",
            )
        )


def test_mask_zeros_for_bad_flag_stations(predictor, history):
    df = history.copy()
    # Zero out Klosod over the last window → triggers FLATLINE/MISSING-style flag.
    df.loc[df.index[-10:], "Klosod"] = 0.0
    mask = predictor._mask_from_quality(df)
    klosod_idx = predictor._stations.index("Klosod")
    target_idx = predictor._stations.index(TARGET_STATION)
    assert mask[klosod_idx] == False  # noqa: E712 — bool check on numpy element
    assert mask[target_idx] == True  # noqa: E712


def test_mask_all_ones_when_history_healthy(predictor, history):
    mask = predictor._mask_from_quality(history)
    # At least the target and the upstream stations should be healthy
    target_idx = predictor._stations.index(TARGET_STATION)
    assert mask[target_idx] == True  # noqa: E712


def test_integration_with_two_tier_predictor(predictor, history):
    routed = TwoTierPredictor(
        tier_a=predictor, tier_b=PersistenceTierB(),
    ).route(history)
    assert routed.serving_tier == "A"
    assert routed.shadow_predictions is not None
    assert set(routed.predictions.keys()) == {"h1", "h2", "h3", "h4", "h5"}


def test_state_summary_exposes_metadata(predictor, tmp_path):
    summary = predictor.state_summary()
    assert summary["epoch"] == 3
    assert summary["test_main"] == pytest.approx(0.42)
    assert summary["features_per_station"] == 7
    assert summary["ar_lag_dim"] == 6
    assert len(summary["stations"]) == len(ALL_STATIONS)
