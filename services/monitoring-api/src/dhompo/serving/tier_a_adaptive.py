"""Predictor adaptive Tier-A untuk stack serving dua-tier."""

from __future__ import annotations

import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from dhompo.config import PROJECT_ROOT
from dhompo.data.loader import ALL_STATIONS
from dhompo.data.tier_a_features import (
    AR_LAG_DIM,
    FEATURES_PER_STATION,
    build_ar_lags,
    build_per_station_features,
)
from dhompo.etl.quality import (
    BAD_FLAGS,
    QualityConfig,
    QualityFlag,
    compute_quality_flags,
    latest_flags,
)
from dhompo.serving.file_predictor import PredictionResult

_BAD_FLAG_VALUES: frozenset[str] = frozenset(f.value for f in BAD_FLAGS)
_DEFAULT_CHECKPOINT_DIR = PROJECT_ROOT / "artifacts" / "tier_a_adaptive"


@dataclass(frozen=True)
class TierAAdaptiveArtifacts:
    checkpoint: Path
    normalizer: Path

    @classmethod
    def from_dir(cls, directory: str | Path) -> "TierAAdaptiveArtifacts":
        d = Path(directory)
        return cls(checkpoint=d / "best.pt", normalizer=d / "normalizer.pkl")


class TierAAdaptivePredictor:
    """Wrapper inferensi untuk checkpoint ``AdaptiveTierA`` yang sudah dilatih."""

    def __init__(
        self,
        artifacts: TierAAdaptiveArtifacts | None = None,
        device: str = "cpu",
    ) -> None:
        try:
            import torch
        except ImportError as exc:
            raise ImportError(
                "TierAAdaptivePredictor needs PyTorch. "
                "Install via: pip install -r requirements-torch.txt"
            ) from exc

        from dhompo.models.adaptive import AdaptiveTierA, AdaptiveTierAConfig
        from dhompo.training.normalizer import Normalizer

        self._torch = torch
        self._artifacts = artifacts or TierAAdaptiveArtifacts.from_dir(
            _DEFAULT_CHECKPOINT_DIR
        )
        if not self._artifacts.checkpoint.exists():
            raise FileNotFoundError(
                f"Tier-A checkpoint missing: {self._artifacts.checkpoint}. "
                "Run `python training/dhompo/run_tier_a_adaptive.py` first."
            )
        if not self._artifacts.normalizer.exists():
            raise FileNotFoundError(
                f"Tier-A normalizer missing: {self._artifacts.normalizer}."
            )

        ckpt = torch.load(
            self._artifacts.checkpoint, map_location=device, weights_only=False,
        )
        config: AdaptiveTierAConfig = ckpt["config"]
        self._config = config
        self._stations: list[str] = list(config.stations)
        self._device = device

        self._model = AdaptiveTierA(config).to(device)
        self._model.load_state_dict(ckpt["model_state"])
        self._model.eval()

        with open(self._artifacts.normalizer, "rb") as fh:
            self._normalizer: Normalizer = pickle.load(fh)

        self._epoch: int = int(ckpt.get("epoch", -1))
        self._test_main: float = float(ckpt.get("test_main", float("nan")))

    @property
    def backend_name(self) -> str:
        return "tier_a_adaptive"

    def model_mapping(self) -> dict[str, str]:
        return {f"h{h}": "tier_a_adaptive" for h in range(1, 6)}

    def predict_from_history(self, history: pd.DataFrame) -> PredictionResult:
        if len(history) < AR_LAG_DIM + 1:
            raise ValueError(
                f"History needs at least {AR_LAG_DIM + 1} rows; got {len(history)}."
            )

        feats = build_per_station_features(history, stations=self._stations)
        ar = build_ar_lags(history)
        if np.isnan(feats[-1]).any():
            raise ValueError(
                "Last-row features contain NaN; need a longer warm-up history."
            )
        if np.isnan(ar[-1]).any():
            raise ValueError(
                "Last-row AR lags contain NaN; the target column has gaps."
            )

        mask = self._mask_from_quality(history)
        feats_t = self._torch.from_numpy(feats[-1:]).to(self._device)
        ar_t = self._torch.from_numpy(ar[-1:]).to(self._device)
        feats_t, ar_t = self._normalizer.apply(feats_t, ar_t)
        mask_t = self._torch.from_numpy(mask[None, :]).to(self._device)

        with self._torch.no_grad():
            horizons, _ = self._model(feats_t, mask_t, ar_t)
        h_vec = horizons.squeeze(0).cpu().numpy()
        predictions = {f"h{h}": round(float(h_vec[h - 1]), 4) for h in range(1, 6)}

        return PredictionResult(
            predictions=predictions,
            model_version=self._model_version_string(),
            confidence="high",
        )

    def _mask_from_quality(
        self, history: pd.DataFrame, config: QualityConfig | None = None,
    ) -> np.ndarray:
        flags_df = compute_quality_flags(history, config)
        flags = latest_flags(flags_df)
        out = np.ones(len(self._stations), dtype=bool)
        for idx, station in enumerate(self._stations):
            flag = flags.get(station, QualityFlag.MISSING.value)
            if flag in _BAD_FLAG_VALUES:
                out[idx] = False
        return out

    def _model_version_string(self) -> str:
        epoch_tag = f"e{self._epoch}" if self._epoch >= 0 else "e?"
        return f"tier_a_adaptive_{epoch_tag}"

    def state_summary(self) -> dict[str, Any]:
        return {
            "checkpoint": str(self._artifacts.checkpoint),
            "normalizer": str(self._artifacts.normalizer),
            "stations": self._stations,
            "epoch": self._epoch,
            "test_main": self._test_main,
            "features_per_station": FEATURES_PER_STATION,
            "ar_lag_dim": AR_LAG_DIM,
            "device": self._device,
        }
