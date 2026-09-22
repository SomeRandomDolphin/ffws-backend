"""Inferensi multi-stasiun untuk simulator + korektor residual Fase 3."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from dhompo.config import PROJECT_ROOT
from dhompo.data.features import build_multistation_features
from dhompo.data.network import load_network
from dhompo.data.rainfall_sim import simulate_rainfall
from dhompo.data.routing_sim import forecast_levels
from dhompo.data.scenarios import load_simulator_parameters

DEFAULT_ARTIFACT_DIR = PROJECT_ROOT / "models" / "sklearn" / "hybrid"
DEFAULT_SIMULATOR_PARAMETERS = (
    PROJECT_ROOT / "configs" / "dhompo" / "simulator_calibrated.yaml"
)


@dataclass(frozen=True)
class MultiStationPredictionResult:
    """Prediksi h1..h6 untuk seluruh stasiun beserta backbone simulator."""

    predictions: dict[str, dict[str, float]]
    simulator_predictions: dict[str, dict[str, float]]
    model_version: str
    future_rainfall_mode: str


@dataclass(frozen=True)
class EnsembleMultiStationPredictionResult:
    """Median dan sebaran empiris dari ensemble skenario hujan."""

    predictions: dict[str, dict[str, float]]
    simulator_predictions: dict[str, dict[str, float]]
    scenario_spread: dict[str, dict[str, dict[str, float]]]
    scenario_count: int
    model_version: str
    future_rainfall_mode: str


class HybridMultiStationPredictor:
    """Muat artefak Fase 3 dan prediksi dari window observasi terakhir."""

    def __init__(
        self,
        artifact_dir: str | Path | None = None,
        simulator_parameter_path: str | Path | None = None,
    ) -> None:
        directory = Path(artifact_dir) if artifact_dir else DEFAULT_ARTIFACT_DIR
        metadata_path = directory / "metadata.json"
        routing_path = directory / "routing_parameters.joblib"
        if not metadata_path.exists() or not routing_path.exists():
            raise FileNotFoundError(
                f"Artefak hybrid tidak lengkap di {directory}. "
                "Jalankan training/dhompo/train_hybrid.py."
            )
        self._directory = directory
        self._metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        self._stations = list(self._metadata["stations"])
        self._horizons = [int(h) for h in self._metadata["horizons"]]
        self._history_rows = int(self._metadata["history_rows"])
        self._base_features = list(self._metadata["base_features"])
        self._routing_parameters = joblib.load(routing_path)
        parameter_path = (
            Path(simulator_parameter_path)
            if simulator_parameter_path
            else DEFAULT_SIMULATOR_PARAMETERS
        )
        self._rainfall_parameters = None
        if parameter_path.exists():
            self._rainfall_parameters, _ = load_simulator_parameters(parameter_path)
        self._residual_models = {}
        for horizon in self._horizons:
            model_path = directory / f"residual_h{horizon}.joblib"
            if not model_path.exists():
                raise FileNotFoundError(f"Model residual tidak ditemukan: {model_path}.")
            self._residual_models[horizon] = joblib.load(model_path)
        self._network = load_network()

    @property
    def backend_name(self) -> str:
        return "hybrid_multistation"

    @property
    def stations(self) -> list[str]:
        return list(self._stations)

    @property
    def horizons(self) -> list[int]:
        return list(self._horizons)

    def model_mapping(self) -> dict[str, str]:
        return {
            f"h{horizon}": f"hybrid_residual_h{horizon}"
            for horizon in self._horizons
        }

    def _latest_features(self, history: pd.DataFrame) -> pd.DataFrame:
        missing = [station for station in self._stations if station not in history.columns]
        if missing:
            raise ValueError(f"Riwayat tidak memuat stasiun: {missing}.")
        if len(history) < self._history_rows:
            raise ValueError(
                f"History needs at least {self._history_rows} rows; got {len(history)}."
            )
        if not isinstance(history.index, pd.DatetimeIndex):
            raise ValueError("Index riwayat harus DatetimeIndex.")
        X = build_multistation_features(history[self._stations])
        if history.index[-1] not in X.index:
            raise ValueError("Fitur waktu terakhir tidak valid; periksa gap/NaN riwayat.")
        X_latest = X.loc[[history.index[-1]]]
        missing_features = [column for column in self._base_features if column not in X_latest]
        if missing_features:
            raise ValueError(f"Fitur model tidak tersedia: {missing_features}.")
        return X_latest[self._base_features]

    def _hybrid_members(
        self,
        X_latest: pd.DataFrame,
        simulator_members: dict[int, np.ndarray],
    ) -> dict[int, np.ndarray]:
        output = {}
        for horizon in self._horizons:
            simulator_values = simulator_members[horizon]
            member_count = simulator_values.shape[0]
            base_batch = pd.DataFrame(
                np.repeat(X_latest.to_numpy(), member_count, axis=0),
                columns=X_latest.columns,
            )
            simulator_frame = pd.DataFrame(
                simulator_values,
                columns=[f"simulator_{station}" for station in self._stations],
            )
            residual = self._residual_models[horizon].predict(
                pd.concat([base_batch, simulator_frame], axis=1),
            )
            output[horizon] = simulator_values + residual
        return output

    def predict_from_history(
        self,
        history: pd.DataFrame,
        future_rainfall: pd.Series | None = None,
        historical_rainfall: pd.Series | None = None,
    ) -> MultiStationPredictionResult:
        """Prediksi +1..+6 jam; default mengasumsikan hujan masa depan nol."""
        X_latest = self._latest_features(history)
        maximum_step = max(self._horizons) * 2
        future_index = pd.date_range(
            history.index[-1] + pd.Timedelta("30min"),
            periods=maximum_step,
            freq="30min",
        )
        rainfall_mode = "provided"
        if future_rainfall is None:
            rainfall = pd.Series(0.0, index=future_index, name="rain_mm")
            rainfall_mode = "zero"
        else:
            if len(future_rainfall) < maximum_step:
                raise ValueError(
                    f"future_rainfall membutuhkan {maximum_step} langkah; "
                    f"diterima {len(future_rainfall)}."
                )
            rainfall = pd.Series(
                pd.to_numeric(future_rainfall.iloc[:maximum_step], errors="coerce")
                .fillna(0.0)
                .clip(lower=0.0)
                .to_numpy(),
                index=future_index,
                name="rain_mm",
            )

        simulator = forecast_levels(
            history[self._stations].tail(self._history_rows),
            rainfall,
            self._routing_parameters,
            self._network,
            historical_rainfall=historical_rainfall,
        )
        simulator_members = {
            horizon: simulator.iloc[[horizon * 2 - 1]][self._stations].to_numpy()
            for horizon in self._horizons
        }
        hybrid_members = self._hybrid_members(X_latest, simulator_members)
        predictions: dict[str, dict[str, float]] = {}
        simulator_predictions: dict[str, dict[str, float]] = {}
        for horizon in self._horizons:
            simulator_values = simulator.iloc[horizon * 2 - 1][self._stations]
            hybrid = hybrid_members[horizon][0]
            label = f"h{horizon}"
            predictions[label] = {
                station: round(float(value), 4)
                for station, value in zip(self._stations, hybrid)
            }
            simulator_predictions[label] = {
                station: round(float(value), 4)
                for station, value in simulator_values.items()
            }

        return MultiStationPredictionResult(
            predictions=predictions,
            simulator_predictions=simulator_predictions,
            model_version="hybrid_residual_hist_gradient_boosting_v1",
            future_rainfall_mode=rainfall_mode,
        )

    def predict_ensemble_from_history(
        self,
        history: pd.DataFrame,
        scenario_count: int = 20,
        seed: int = 42,
        historical_rainfall: pd.Series | None = None,
    ) -> EnsembleMultiStationPredictionResult:
        """Prediksi probabilistik dari ensemble hujan Markov-Gamma."""
        if self._rainfall_parameters is None:
            raise FileNotFoundError(
                "Parameter hujan terkalibrasi tidak tersedia. "
                "Jalankan scripts/build_synthetic.py --calibrate-only."
            )
        if not 2 <= scenario_count <= 200:
            raise ValueError("scenario_count harus berada antara 2 dan 200.")
        if not isinstance(history.index, pd.DatetimeIndex):
            raise ValueError("Index riwayat harus DatetimeIndex.")

        hybrid_members, simulator_members = self.ensemble_members_from_history(
            history,
            scenario_count=scenario_count,
            seed=seed,
            historical_rainfall=historical_rainfall,
        )

        predictions: dict[str, dict[str, float]] = {}
        simulator_predictions: dict[str, dict[str, float]] = {}
        scenario_spread: dict[str, dict[str, dict[str, float]]] = {}
        for horizon in self._horizons:
            label = f"h{horizon}"
            predictions[label] = {}
            simulator_predictions[label] = {}
            scenario_spread[label] = {}
            for station_idx, station in enumerate(self._stations):
                hybrid_values = hybrid_members[horizon][:, station_idx]
                simulator_values = simulator_members[horizon][:, station_idx]
                p10, p50, p90 = np.quantile(hybrid_values, [0.1, 0.5, 0.9])
                predictions[label][station] = round(float(p50), 4)
                simulator_predictions[label][station] = round(
                    float(np.median(simulator_values)), 4,
                )
                scenario_spread[label][station] = {
                    "p10": round(float(p10), 4),
                    "p50": round(float(p50), 4),
                    "p90": round(float(p90), 4),
                }

        return EnsembleMultiStationPredictionResult(
            predictions=predictions,
            simulator_predictions=simulator_predictions,
            scenario_spread=scenario_spread,
            scenario_count=scenario_count,
            model_version="hybrid_residual_hist_gradient_boosting_v1",
            future_rainfall_mode="markov_gamma_ensemble",
        )

    def ensemble_members_from_history(
        self,
        history: pd.DataFrame,
        scenario_count: int = 20,
        seed: int = 42,
        historical_rainfall: pd.Series | None = None,
    ) -> tuple[dict[int, np.ndarray], dict[int, np.ndarray]]:
        """Kembalikan member hybrid/simulator mentah untuk evaluasi probabilistik."""
        if self._rainfall_parameters is None:
            raise FileNotFoundError(
                "Parameter hujan terkalibrasi tidak tersedia. "
                "Jalankan scripts/build_synthetic.py --calibrate-only."
            )
        if not 2 <= scenario_count <= 200:
            raise ValueError("scenario_count harus berada antara 2 dan 200.")
        if not isinstance(history.index, pd.DatetimeIndex):
            raise ValueError("Index riwayat harus DatetimeIndex.")

        X_latest = self._latest_features(history)
        maximum_step = max(self._horizons) * 2
        start = history.index[-1] + pd.Timedelta("30min")
        simulator_runs = []
        for member in range(scenario_count):
            rainfall = simulate_rainfall(
                start,
                maximum_step,
                self._rainfall_parameters,
                seed=seed + member,
            )
            simulator_runs.append(forecast_levels(
                history[self._stations].tail(self._history_rows),
                rainfall,
                self._routing_parameters,
                self._network,
                historical_rainfall=historical_rainfall,
            ))

        simulator_members = {
            horizon: np.stack([
                run.iloc[horizon * 2 - 1][self._stations].to_numpy(dtype=float)
                for run in simulator_runs
            ])
            for horizon in self._horizons
        }
        hybrid_members = self._hybrid_members(X_latest, simulator_members)
        return hybrid_members, simulator_members


__all__ = [
    "EnsembleMultiStationPredictionResult",
    "HybridMultiStationPredictor",
    "MultiStationPredictionResult",
]
