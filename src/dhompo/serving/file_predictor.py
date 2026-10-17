"""FilePredictor: Memuat model terbaik per horizon berdasarkan hasil riset"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import joblib
import pandas as pd

from dhompo.config import PROJECT_ROOT
from dhompo.data.features import build_forecast_features


@dataclass
class PredictionResult:
    predictions: dict[str, float]  # {"h1": 1.23, "h2": 1.45, ...}
    model_version: str
    confidence: str

# Model terbaik per horizon (dari xls_11_model_metrics.xlsx)
BEST_MODEL_FILES: dict[int, str] = {
    1: "model_xgboost_h1.pkl",
    2: "model_gradient_boosting_h2.pkl",
    3: "model_gradient_boosting_h3.pkl",
    4: "model_lasso_l1_h4.pkl",
    5: "model_lasso_l1_h5.pkl",
}

# Model yang butuh fitur di-scale sebelum predict (linear models)
SCALED_HORIZONS: set[int] = {4, 5}  # Lasso
SCALER_FILENAME = "scaler.pkl"

_DEFAULT_MODEL_DIR = PROJECT_ROOT / "models" / "sklearn"


class FilePredictor:
    """Serve prediksi multi-horizon dari file .pkl lokal.

    Parameters
    ----------
    model_dir:
        Path ke direktori berisi .pkl models dan scaler.pkl.
        Default: models/sklearn/ relatif dari project root.

    Examples
    --------
    >>> predictor = FilePredictor()
    >>> result = predictor.predict_from_history(df_last_24h)
    >>> result.predictions  # {"h1": 1.23, "h2": 1.45, ...}
    """

    def __init__(self, model_dir: str | Path | None = None) -> None:
        self._model_dir = Path(model_dir) if model_dir else _DEFAULT_MODEL_DIR
        self._models: dict[int, object] = {}
        self._scaler = None  # diisi oleh _load_all jika scaler.pkl ada
        self._load_all()

    def _load_all(self) -> None:
        """Load semua model terbaik + scaler saat inisialisasi."""
        # Load scaler (dibutuhkan oleh linear models: Lasso h4, h5)
        scaler_path = self._model_dir / SCALER_FILENAME
        if scaler_path.exists():
            self._scaler = joblib.load(scaler_path)

        for h, fname in BEST_MODEL_FILES.items():
            path = self._model_dir / fname
            if not path.exists():
                raise FileNotFoundError(
                    f"Model file tidak ditemukan: {path}\n"
                    f"Pastikan notebook 02 sudah dieksekusi."
                )
            self._models[h] = joblib.load(path)

    @property
    def backend_name(self) -> str:
        return "file"

    def model_mapping(self) -> dict[str, str]:
        return {f"h{h}": fname for h, fname in BEST_MODEL_FILES.items()}

    def predict_from_history(
        self,
        history: pd.DataFrame,
        horizons: list[int] | None = None,
    ) -> PredictionResult:
        """Prediksi dari DataFrame riwayat sensor.

        Parameters
        ----------
        history:
            DataFrame dengan DatetimeIndex frekuensi 30 menit.
            Minimal 24 baris (= 12 jam) agar rolling features tidak NaN.
            Kolom = nama stasiun.
        horizons:
            List horizon yang diprediksi. Default: [1, 2, 3, 4, 5].

        Returns
        -------
        PredictionResult
            predictions: {"h1": float, "h2": float, ..., "h5": float}

        Raises
        ------
        ValueError
            Jika history kurang dari 24 baris atau fitur gagal dibangun.
        """
        if horizons is None:
            horizons = [1, 2, 3, 4, 5]

        if len(history) < 24:
            raise ValueError(
                f"History harus minimal 24 baris (12 jam). "
                f"Diterima: {len(history)} baris."
            )

        feats = build_forecast_features(history)
        if feats.empty:
            raise ValueError(
                "Feature matrix kosong setelah dropna. "
                "Pastikan kolom stasiun tidak semuanya NaN."
            )

        X_raw = feats.iloc[[-1]]  # prediksi dari baris/timestep terakhir

        # Scale sekali — dipakai oleh linear models (Lasso h4, h5)
        X_scaled = None
        if self._scaler is not None:
            import numpy as np
            X_scaled = pd.DataFrame(
                self._scaler.transform(X_raw),
                columns=X_raw.columns,
                index=X_raw.index,
            )

        predictions: dict[str, float] = {}
        for h in horizons:
            X = X_scaled if (h in SCALED_HORIZONS and X_scaled is not None) else X_raw
            pred = float(self._models[h].predict(X)[0])
            predictions[f"h{h}"] = round(pred, 4)

        return PredictionResult(
            predictions=predictions,
            model_version="file_v1",
            confidence="high",
        )
