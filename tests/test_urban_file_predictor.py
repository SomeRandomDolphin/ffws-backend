from __future__ import annotations

import json

import joblib
import numpy as np
import pandas as pd

from dhompo.data.urban_features import build_urban_forecast_features
from dhompo.data.urban_loader import QUALITY_OK
from dhompo.serving.urban_file_predictor import UrbanFilePredictor


class ConstantModel:
    def __init__(self, value: float):
        self.value = value

    def predict(self, X):
        return np.full(len(X), self.value)


def test_urban_file_predictor_loads_best_models_and_predicts(tmp_path):
    idx = pd.date_range("2026-01-01", periods=30, freq="30min", name="Datetime")
    values = pd.DataFrame(
        {"ketinggian_lokasi_1_hang_tuah": np.arange(30, dtype=float)},
        index=idx,
    )
    flags = pd.DataFrame(
        {"ketinggian_lokasi_1_hang_tuah": [QUALITY_OK] * 30},
        index=idx,
    )
    X = build_urban_forecast_features(
        values,
        feature_columns=["ketinggian_lokasi_1_hang_tuah"],
        quality_flags=flags,
        lag_steps=[1, 2, 3],
        rolling_windows=[(6, "3h"), (12, "6h"), (24, "12h")],
    )

    model_path = tmp_path / "urban_constant_h1.pkl"
    joblib.dump(ConstantModel(12.34567), model_path)
    joblib.dump(ConstantModel(0.0), tmp_path / "standard_scaler_global.pkl")
    metadata = {
        "target_column": "ketinggian_lokasi_1_hang_tuah",
        "source_signals": ["ketinggian_lokasi_1_hang_tuah"],
        "feature_columns": list(X.columns),
        "best_per_horizon": {
            "h1": {
                "model_key": "constant",
                "nse": 1.0,
                "path": str(model_path),
            }
        },
        "models": {
            "h1:constant": {
                "path": str(model_path),
                "use_scaled": False,
                "train_metrics": {},
                "test_metrics": {},
            }
        },
        "model_version": "urban_file_v2",
        "uses_pump_telemetry": True,
        "prediction_intervals": {
            "h1": {"absolute_error_p90": 2.0},
        },
    }
    (tmp_path / "training_metadata.json").write_text(
        json.dumps(metadata),
        encoding="utf-8",
    )

    predictor = UrbanFilePredictor(model_dir=tmp_path)
    result = predictor.predict_from_history(values, flags)

    assert result.predictions == {"h1": 12.3457}
    assert result.intervals == {"h1": (10.3457, 14.3457)}
    assert result.target_column == "ketinggian_lokasi_1_hang_tuah"
    assert result.model_version == "urban_file_v2"
    assert result.uses_pump_telemetry is True
    assert result.fallback_horizons == ()

    metadata["promotion"] = {
        "required_to_beat_persistence": True,
        "eligible_by_horizon": {"h1": False},
    }
    (tmp_path / "training_metadata.json").write_text(
        json.dumps(metadata), encoding="utf-8"
    )
    fallback = UrbanFilePredictor(model_dir=tmp_path).predict_from_history(values, flags)

    assert fallback.predictions == {"h1": 29.0}
    assert fallback.intervals == {}
    assert fallback.fallback_horizons == ("h1",)
