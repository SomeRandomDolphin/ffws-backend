"""Predictor dua-tier: Tier-A normal, Tier-B fallback saat telemetri mati."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import pandas as pd

from dhompo.data.clusters import (
    PRIMARY_STATION_BY_HORIZON,
    UPSTREAM_TELEMETRY,
)
from dhompo.data.loader import TARGET_STATION
from dhompo.etl.quality import (
    BAD_FLAGS,
    QualityConfig,
    QualityFlag,
    compute_quality_flags,
    latest_flags,
)
from dhompo.serving.file_predictor import FilePredictor, PredictionResult


class HorizonPredictor(Protocol):
    def predict_from_history(self, history: pd.DataFrame) -> PredictionResult: ...

    def model_mapping(self) -> dict[str, str]: ...


@dataclass
class RoutedPrediction:
    """Hasil prediksi dua-tier yang sudah di-route beserta metadata degradasi."""

    predictions: dict[str, float]
    serving_tier: str  # "A" atau "B"
    degradation: dict[str, str]
    shadow_predictions: dict[str, float] | None
    quality_flags: dict[str, str]
    model_version: str
    confidence: str


class PersistenceTierB:
    """Floor Tier-B: forecast = nilai Dhompo terakhir untuk semua horizon."""

    @property
    def backend_name(self) -> str:
        return "tier_b_persistence"

    def model_mapping(self) -> dict[str, str]:
        return {f"h{h}": "persistence_dhompo" for h in range(1, 6)}

    def predict_from_history(self, history: pd.DataFrame) -> PredictionResult:
        if TARGET_STATION not in history.columns:
            raise ValueError(
                f"Tier-B requires column '{TARGET_STATION}' in history."
            )
        last_value = float(history[TARGET_STATION].dropna().iloc[-1])
        predictions = {f"h{h}": round(last_value, 4) for h in range(1, 6)}
        return PredictionResult(
            predictions=predictions,
            model_version="tier_b_persistence_v0",
            confidence="floor",
        )


class TwoTierPredictor:
    """Route prediksi antara Tier-A dan Tier-B berdasarkan kesehatan telemetri."""

    def __init__(
        self,
        tier_a: HorizonPredictor | None = None,
        tier_b: HorizonPredictor | None = None,
        quality_config: QualityConfig | None = None,
    ) -> None:
        self._tier_a = tier_a if tier_a is not None else FilePredictor()
        self._tier_b = tier_b if tier_b is not None else PersistenceTierB()
        self._quality_config = quality_config

    @property
    def backend_name(self) -> str:
        # Proxy nama Tier-A; pembeda tier ada di PredictResponse.serving_tier.
        return getattr(self._tier_a, "backend_name", "file")

    def model_mapping(self) -> dict[str, str]:
        return self._tier_a.model_mapping()

    def fallback_model_mapping(self) -> dict[str, str]:
        return self._tier_b.model_mapping()

    def route(self, history: pd.DataFrame) -> RoutedPrediction:
        flags_df = compute_quality_flags(history, self._quality_config)
        flags = latest_flags(flags_df)

        bad_telemetry = [
            station for station in UPSTREAM_TELEMETRY
            if flags.get(station, QualityFlag.MISSING.value) in {f.value for f in BAD_FLAGS}
        ]
        all_telemetry_down = len(bad_telemetry) == len(UPSTREAM_TELEMETRY)

        degradation: dict[str, str] = {}
        for horizon, primary in PRIMARY_STATION_BY_HORIZON.items():
            primary_flag = flags.get(primary, QualityFlag.MISSING.value)
            if primary_flag in {f.value for f in BAD_FLAGS}:
                degradation[f"h{horizon}"] = (
                    f"PRIMARY_STATION_{primary_flag}:{primary}"
                )

        if all_telemetry_down:
            served = self._tier_b.predict_from_history(history)
            shadow: dict[str, float] | None = None
            serving_tier = "B"
            model_version = served.model_version
            confidence = served.confidence
        else:
            served = self._tier_a.predict_from_history(history)
            shadow_result = self._tier_b.predict_from_history(history)
            shadow = shadow_result.predictions
            serving_tier = "A"
            model_version = served.model_version
            confidence = served.confidence

        return RoutedPrediction(
            predictions=served.predictions,
            serving_tier=serving_tier,
            degradation=degradation,
            shadow_predictions=shadow,
            quality_flags=flags,
            model_version=model_version,
            confidence=confidence,
        )

    def predict_from_history(self, history: pd.DataFrame) -> PredictionResult:
        """API prediksi titik tanpa metadata routing."""
        routed = self.route(history)
        return PredictionResult(
            predictions=routed.predictions,
            model_version=routed.model_version,
            confidence=routed.confidence,
        )
