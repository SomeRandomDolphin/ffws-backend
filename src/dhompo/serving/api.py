"""
Usage
-----
    uvicorn dhompo.serving.api:app --host 0.0.0.0 --port 8000 --reload

Endpoints
---------
    GET  /health         → status + mapping model per horizon
    GET  /stations       → daftar nama stasiun yang wajib ada di input
    POST /predict        → prediksi dari riwayat sensor (DataFrame-like JSON)
"""

from __future__ import annotations

from typing import Any

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from dhompo.data.loader import TARGET_STATION, UPSTREAM_STATIONS
from dhompo.serving.file_predictor import FilePredictor


app = FastAPI(
    title="Dhompo Forecast API",
    description="Prediksi tinggi muka air Sungai Dhompo multi-horizon (30 min – 2.5 jam).",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Load model sekali saat startup 
_predictor: FilePredictor | None = None


@app.on_event("startup")
def _load_models() -> None:
    global _predictor
    _predictor = FilePredictor()


def _get_predictor() -> FilePredictor:
    if _predictor is None:
        raise HTTPException(status_code=503, detail="Predictor belum siap.")
    return _predictor


# Schemas
class HistoryRequest(BaseModel):
    """Input payload untuk endpoint /predict.

    Contoh minimal (24 baris, 12 jam, interval 30 menit):
    ```json
    {
      "timestamps": ["2026-01-01T00:00:00", "2026-01-01T00:30:00", ...],
      "stations": {
        "Dhompo":        [1.20, 1.21, ...],
        "Bd. Suwoto":    [0.85, 0.86, ...],
        "Krajan Timur":  [0.92, 0.93, ...],
        ...
      },
      "horizons": [1, 2, 3, 4, 5]
    }
    ```
    """

    timestamps: list[str] = Field(
        ...,
        description="List ISO datetime, frekuensi 30 menit, minimal 24 entri.",
        min_length=24,
    )
    stations: dict[str, list[float]] = Field(
        ...,
        description=(
            "Mapping nama stasiun → list nilai sensor. "
            "Harus memuat semua stasiun upstream + target (Dhompo). "
            "Panjang tiap list harus sama dengan panjang timestamps."
        ),
    )
    horizons: list[int] | None = Field(
        default=None,
        description="List horizon yang diprediksi (1–5). Default: semua [1,2,3,4,5].",
    )


class PredictResponse(BaseModel):
    predictions: dict[str, float]
    model_version: str
    confidence: str
    last_timestamp: str


class HealthResponse(BaseModel):
    status: str
    models: dict[str, str]


class StationsResponse(BaseModel):
    target: str
    upstream: list[str]
    required_columns: list[str]


# Endpoints
@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Cek status API + mapping model per horizon."""
    predictor = _get_predictor()
    return HealthResponse(status="ok", models=predictor.model_mapping())


@app.get("/stations", response_model=StationsResponse)
def stations() -> StationsResponse:
    """Daftar nama stasiun yang wajib ada pada payload `/predict`."""
    required = UPSTREAM_STATIONS + [TARGET_STATION]
    return StationsResponse(
        target=TARGET_STATION,
        upstream=UPSTREAM_STATIONS,
        required_columns=required,
    )


@app.post("/predict", response_model=PredictResponse)
def predict(req: HistoryRequest) -> PredictResponse:
    """Prediksi tinggi muka air Dhompo untuk horizon 1–5 (setiap horizon = 30 menit)."""
    predictor = _get_predictor()

    # Validasi: semua kolom stasiun harus ada
    required = set(UPSTREAM_STATIONS + [TARGET_STATION])
    missing = required - set(req.stations.keys())
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Kolom stasiun wajib tidak lengkap. Kurang: {sorted(missing)}",
        )

    # Validasi: panjang tiap kolom harus sama dengan timestamps
    n = len(req.timestamps)
    for name, values in req.stations.items():
        if len(values) != n:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Panjang '{name}' = {len(values)} tidak sama dengan "
                    f"jumlah timestamps = {n}."
                ),
            )

    # Validasi horizons
    horizons = req.horizons or [1, 2, 3, 4, 5]
    invalid = [h for h in horizons if h not in {1, 2, 3, 4, 5}]
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=f"Horizon tidak valid: {invalid}. Hanya 1–5 yang didukung.",
        )

    # Bangun DataFrame
    try:
        index = pd.DatetimeIndex(req.timestamps)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Format timestamp tidak valid: {exc}")

    df = pd.DataFrame(req.stations, index=index).sort_index()

    # Panggil predictor
    try:
        result = predictor.predict_from_history(df, horizons=horizons)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Prediksi gagal: {exc}")

    return PredictResponse(
        predictions=result.predictions,
        model_version=result.model_version,
        confidence=result.confidence,
        last_timestamp=str(df.index[-1]),
    )


# Entrypoint
def main() -> None:
    import uvicorn
    uvicorn.run("dhompo.serving.api:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()
