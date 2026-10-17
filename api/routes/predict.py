from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
from fastapi import APIRouter, HTTPException, Request

from api.schemas import HorizonPredictions, PredictRequest, PredictResponse
from api.predictor_state import get_predictor
from dhompo.serving.two_tier import TwoTierPredictor

router = APIRouter()


def _history_to_dataframe(request: PredictRequest) -> pd.DataFrame:
    """Convert validated PredictRequest.history → DataFrame with DatetimeIndex."""
    records = []
    timestamps = []
    for row in request.history:
        records.append(row.readings)
        timestamps.append(row.timestamp)

    df = pd.DataFrame(records, index=pd.DatetimeIndex(timestamps, name="Datetime"))
    df = df.asfreq("30min")
    return df


def _horizons_from_dict(values: dict[str, float]) -> HorizonPredictions:
    return HorizonPredictions(
        h1=values["h1"], h2=values["h2"], h3=values["h3"],
        h4=values["h4"], h5=values["h5"],
    )


@router.post("/predict", response_model=PredictResponse, tags=["prediction"])
async def predict(request: PredictRequest, http_request: Request) -> PredictResponse:
    """Predict water levels at Dhompo for +1 to +5 hours.

    Requires at least 24 rows (12 hours) of 30-minute historical sensor
    data from all upstream stations and Dhompo. The two-tier router selects
    Tier-A (adaptive) or Tier-B (autoregressive floor) based on telemetry
    health and emits per-horizon degradation metadata.
    """
    try:
        predictor = get_predictor(http_request)
        df = _history_to_dataframe(request)

        if isinstance(predictor, TwoTierPredictor):
            routed = predictor.route(df)
            preds = routed.predictions
            serving_tier = routed.serving_tier
            degradation = routed.degradation
            shadow = (
                _horizons_from_dict(routed.shadow_predictions)
                if routed.shadow_predictions is not None
                else None
            )
            quality_flags = routed.quality_flags
            models = (
                predictor.model_mapping() if serving_tier == "A"
                else predictor.fallback_model_mapping()
            )
        else:
            result = predictor.predict_from_history(df)
            preds = result.predictions
            serving_tier = "A"
            degradation = {}
            shadow = None
            quality_flags = {}
            models = (
                predictor.model_mapping()
                if hasattr(predictor, "model_mapping") else {}
            )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Model unavailable: {exc}") from exc

    last_ts = request.history[-1].timestamp
    backend = getattr(
        predictor, "backend_name",
        getattr(http_request.app.state, "predictor_backend", "file"),
    )
    return PredictResponse(
        predictions=_horizons_from_dict(preds),
        backend=backend,
        models=models,
        timestamp=last_ts,
        prediction_time=datetime.now(timezone.utc),
        serving_tier=serving_tier,
        degradation=degradation,
        shadow_predictions=shadow,
        quality_flags=quality_flags,
    )
