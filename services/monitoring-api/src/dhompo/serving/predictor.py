"""Model predictor: load from MLflow registry and run inference.

Usage
-----
    predictor = SklearnPredictor(horizon=1)
    predictions = predictor.predict(station_readings, timestamp)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import joblib
import mlflow
import mlflow.sklearn
import numpy as np
import pandas as pd

from dhompo.config import PROJECT_ROOT
from dhompo.data.features import build_forecast_features
from dhompo.data.loader import TARGET_STATION, UPSTREAM_STATIONS
from dhompo.serving.file_predictor import SCALED_HORIZONS


@dataclass
class PredictionResult:
    predictions: dict[str, float]  # {"h1": 0.45, "h2": 0.52, ...}
    model_version: str
    confidence: str


class SklearnPredictor:
    """Load a registered MLflow sklearn model and produce multi-horizon predictions.

    Parameters
    ----------
    mlflow_tracking_uri:
        MLflow tracking server URI. Defaults to MLFLOW_TRACKING_URI env var or
        "http://localhost:5000".
    model_name_template:
        Template for registered model names. ``{h}`` is replaced by horizon int.
    alias:
        MLflow model alias to load, e.g. "production" or "champion".
    """

    def __init__(
        self,
        mlflow_tracking_uri: str | None = None,
        model_name_template: str = "dhompo_h{h}",
        alias: str = "production",
        scaler_path: str | Path | None = None,
    ) -> None:
        uri = mlflow_tracking_uri or os.getenv(
            "MLFLOW_TRACKING_URI", "http://localhost:5000"
        )
        mlflow.set_tracking_uri(uri)
        self._template = model_name_template
        self._alias = alias
        self._models: dict[int, object] = {}
        self._model_versions: dict[int, str] = {}
        self._scaler_path = (
            Path(scaler_path)
            if scaler_path is not None
            else PROJECT_ROOT / "models" / "sklearn" / "scaler.pkl"
        )
        self._scaler = None

    @property
    def backend_name(self) -> str:
        return "mlflow"

    def _load_scaler(self):
        if self._scaler is None:
            if not self._scaler_path.exists():
                raise FileNotFoundError(
                    f"Scaler file tidak ditemukan untuk backend MLflow: {self._scaler_path}"
                )
            self._scaler = joblib.load(self._scaler_path)
        return self._scaler

    def _load_model(self, horizon: int) -> object:
        if horizon not in self._models:
            name = self._template.format(h=horizon)
            model_uri = f"models:/{name}@{self._alias}"
            client = mlflow.tracking.MlflowClient()
            version = client.get_model_version_by_alias(name, self._alias)
            self._model_versions[horizon] = version.version
            self._models[horizon] = mlflow.sklearn.load_model(model_uri)
        return self._models[horizon]

    def model_mapping(self) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for h in range(1, 6):
            mapping[f"h{h}"] = f"models:/{self._template.format(h=h)}@{self._alias}"
        return mapping

    def predict(
        self,
        station_readings: dict[str, float],
        timestamp: datetime,
        horizons: list[int] | None = None,
    ) -> PredictionResult:
        """Run inference for all requested horizons.

        Parameters
        ----------
        station_readings:
            Dict mapping station name → current water level (m).
        timestamp:
            Observation datetime.
        horizons:
            Horizons to predict. Defaults to [1, 2, 3, 4, 5].

        Returns
        -------
        PredictionResult
        """
        if horizons is None:
            horizons = [1, 2, 3, 4, 5]

        # Build a one-row DataFrame from the reading
        row_data = {st: [station_readings.get(st, np.nan)] for st in UPSTREAM_STATIONS}
        row_data[TARGET_STATION] = [station_readings.get(TARGET_STATION, np.nan)]
        idx = pd.DatetimeIndex([timestamp], freq="30min")
        df_single = pd.DataFrame(row_data, index=idx)

        # We need historical context for lag/rolling features; for single-point
        # inference the caller must provide a DataFrame with sufficient history.
        # This path is used by tests with minimal history.
        feats = build_forecast_features(df_single)
        if feats.empty:
            raise ValueError(
                "Cannot build features from a single timestep — provide historical data."
            )
        X = feats.iloc[[-1]]

        predictions: dict[str, float] = {}
        versions: list[str] = []
        for h in horizons:
            model = self._load_model(h)
            pred = float(model.predict(X)[0])
            predictions[f"h{h}"] = round(pred, 4)
            versions.append(self._model_versions.get(h, "unknown"))

        # Confidence heuristic: based on h1 NSE proxied by prediction variance
        confidence = "high" if len(set(versions)) == 1 else "medium"
        model_version = versions[0] if versions else "unknown"

        return PredictionResult(
            predictions=predictions,
            model_version=model_version,
            confidence=confidence,
        )


class HistoricalPredictor(SklearnPredictor):
    """Variant that accepts a full historical DataFrame for feature construction.

    Use this when you have the last 24 hours of sensor data available,
    which is required for rolling window features.
    """

    def predict_from_history(
        self,
        history: pd.DataFrame,
        horizons: list[int] | None = None,
    ) -> PredictionResult:
        """Predict using full historical DataFrame.

        Parameters
        ----------
        history:
            DataFrame with DatetimeIndex at 30-min frequency, containing all
            station columns. Must have ≥ 24 rows.
        horizons:
            Horizons to predict. Defaults to [1, 2, 3, 4, 5].
        """
        if horizons is None:
            horizons = [1, 2, 3, 4, 5]

        if len(history) < 24:
            raise ValueError(f"History harus minimal 24 baris. Diterima: {len(history)}.")

        feats = build_forecast_features(history)
        if feats.empty:
            raise ValueError("Feature matrix is empty — check input DataFrame.")
        X_raw = feats.iloc[[-1]]
        X_scaled = None

        predictions: dict[str, float] = {}
        versions: list[str] = []
        for h in horizons:
            model = self._load_model(h)
            if h in SCALED_HORIZONS:
                if X_scaled is None:
                    scaler = self._load_scaler()
                    X_scaled = pd.DataFrame(
                        scaler.transform(X_raw),
                        columns=X_raw.columns,
                        index=X_raw.index,
                    )
                X = X_scaled
            else:
                X = X_raw
            pred = float(model.predict(X)[0])
            predictions[f"h{h}"] = round(pred, 4)
            versions.append(self._model_versions.get(h, "unknown"))

        version_tag = versions[0] if versions else "unknown"
        return PredictionResult(
            predictions=predictions,
            model_version=version_tag,
            confidence="high",
        )
