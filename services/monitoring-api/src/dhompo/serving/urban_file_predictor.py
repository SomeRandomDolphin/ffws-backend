"""File-backed predictor for the urban water-level baseline models."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import pandas as pd

from dhompo.config import PROJECT_ROOT, load_yaml_config, resolve_artifact_path
from dhompo.data.urban_features import build_urban_forecast_features

_DEFAULT_MODEL_DIR = PROJECT_ROOT / "models" / "surabaya" / "urban"
_DEFAULT_CONFIG_PATH = "configs/surabaya/urban_water_level.yaml"


@dataclass(frozen=True)
class UrbanPredictionResult:
    predictions: dict[str, float]
    intervals: dict[str, tuple[float, float]]
    models: dict[str, str]
    target_column: str
    model_version: str
    uses_pump_telemetry: bool
    fallback_horizons: tuple[str, ...]


class UrbanFilePredictor:
    """Serve multi-horizon urban water-level predictions from local artifacts."""

    def __init__(
        self,
        model_dir: str | Path | None = None,
        config_path: str | Path = _DEFAULT_CONFIG_PATH,
    ) -> None:
        self._model_dir = Path(model_dir) if model_dir is not None else _DEFAULT_MODEL_DIR
        self._config_path = config_path
        self._config = load_yaml_config(config_path)
        self._metadata = self._load_metadata()
        self._feature_columns: list[str] = list(self._metadata["feature_columns"])
        self._source_signals: list[str] = list(self._metadata["source_signals"])
        self._target_column = str(self._metadata["target_column"])
        self._target_mode = str(self._metadata.get("target_mode", "level")).lower()
        self._models: dict[int, Any] = {}
        self._use_scaled: dict[int, bool] = {}
        self._model_paths: dict[int, Path] = {}
        self._scaler = self._load_scaler()
        self._load_models()

    @property
    def backend_name(self) -> str:
        return "urban_file"

    @property
    def target_column(self) -> str:
        return self._target_column

    def model_mapping(self) -> dict[str, str]:
        """Return model paths for diagnostics."""
        return {f"h{h}": str(path) for h, path in sorted(self._model_paths.items())}

    @property
    def source_signals(self) -> list[str]:
        return list(self._source_signals)

    def predict_from_history(
        self,
        values: pd.DataFrame,
        quality_flags: pd.DataFrame | None = None,
    ) -> UrbanPredictionResult:
        """Predict from canonical urban history at 30-minute cadence."""

        min_history_rows = int(self._config.get("min_history_rows", 24))
        if len(values) < min_history_rows:
            raise ValueError(
                f"History must contain at least {min_history_rows} rows; got {len(values)}."
            )

        feature_cfg = self._config.get("features", {})
        rolling_windows = [
            (int(w["steps"]), str(w["label"]))
            for w in feature_cfg.get("rolling_windows", [])
        ]
        X_all = build_urban_forecast_features(
            values,
            feature_columns=self._source_signals,
            quality_flags=quality_flags,
            include_quality_flags=bool(feature_cfg.get("include_quality_flags", True)),
            lag_steps=[int(v) for v in feature_cfg.get("lag_steps", [1, 2, 3])],
            rolling_windows=rolling_windows,
            pump_control=feature_cfg.get("pump_control"),
        )
        if X_all.empty:
            raise ValueError("Feature matrix is empty; provide longer warm-up history.")

        X_raw = X_all.iloc[[-1]]
        missing_features = set(self._feature_columns) - set(X_raw.columns)
        if missing_features:
            raise ValueError(f"Missing trained feature columns: {sorted(missing_features)}")
        X_raw = X_raw[self._feature_columns]

        X_scaled = None
        predictions: dict[str, float] = {}
        intervals: dict[str, tuple[float, float]] = {}
        fallback_horizons: list[str] = []
        for horizon, model in sorted(self._models.items()):
            horizon_key = f"h{horizon}"
            promotion = self._metadata.get("promotion", {})
            eligible = promotion.get("eligible_by_horizon", {}).get(horizon_key, True)
            if promotion.get("required_to_beat_persistence", False) and not eligible:
                current_value = values.loc[X_raw.index[0], self._target_column]
                if pd.isna(current_value):
                    raise ValueError(
                        f"Current target value is unavailable for persistence fallback: "
                        f"{self._target_column}"
                    )
                predictions[horizon_key] = round(float(current_value), 4)
                fallback_horizons.append(horizon_key)
                continue
            X = X_raw
            if self._use_scaled[horizon]:
                if X_scaled is None:
                    X_scaled = pd.DataFrame(
                        self._scaler.transform(X_raw),
                        columns=X_raw.columns,
                        index=X_raw.index,
                    )
                X = X_scaled
            raw_pred = float(model.predict(X)[0])
            if self._target_mode in {"delta", "persistence_residual"}:
                current_value = values.loc[X_raw.index[0], self._target_column]
                if pd.isna(current_value):
                    raise ValueError(
                        f"Current target value is unavailable for delta prediction: "
                        f"{self._target_column}"
                    )
                current_level = float(current_value)
                raw_pred = current_level + raw_pred
            predictions[horizon_key] = round(raw_pred, 4)
            radius = self._metadata.get("prediction_intervals", {}).get(
                horizon_key, {}
            ).get("absolute_error_p90")
            if radius is not None:
                intervals[horizon_key] = (
                    round(max(0.0, raw_pred - float(radius)), 4),
                    round(raw_pred + float(radius), 4),
                )

        return UrbanPredictionResult(
            predictions=predictions,
            intervals=intervals,
            models=self.model_mapping(),
            target_column=self._target_column,
            model_version=str(self._metadata.get("model_version", "urban_file_v1")),
            uses_pump_telemetry=bool(self._metadata.get("uses_pump_telemetry", False)),
            fallback_horizons=tuple(fallback_horizons),
        )

    def _load_metadata(self) -> dict[str, Any]:
        metadata_path = self._model_dir / "training_metadata.json"
        if not metadata_path.exists():
            raise FileNotFoundError(f"Urban training metadata not found: {metadata_path}")
        return json.loads(metadata_path.read_text(encoding="utf-8"))

    def _load_scaler(self) -> Any:
        scaler_path = self._model_dir / "standard_scaler_global.pkl"
        if not scaler_path.exists():
            raise FileNotFoundError(f"Urban scaler not found: {scaler_path}")
        return joblib.load(scaler_path)

    def _load_models(self) -> None:
        for horizon_key, info in self._metadata["best_per_horizon"].items():
            horizon = int(horizon_key.removeprefix("h"))
            model_key = str(info["model_key"])
            model_path = resolve_artifact_path(self._model_dir, info["path"])
            if not model_path.exists():
                raise FileNotFoundError(f"Urban model not found: {model_path}")

            model_meta = self._metadata["models"][f"h{horizon}:{model_key}"]
            self._models[horizon] = joblib.load(model_path)
            self._use_scaled[horizon] = bool(model_meta["use_scaled"])
            self._model_paths[horizon] = model_path
