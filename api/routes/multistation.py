"""Endpoint prediksi hybrid untuk seluruh 15 stasiun."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
from fastapi import APIRouter, HTTPException, Request

from api.predictor_state import get_hybrid_predictor
from api.schemas import MultiStationPredictRequest, MultiStationPredictResponse

router = APIRouter()


def _history_to_dataframe(request: MultiStationPredictRequest) -> pd.DataFrame:
    return pd.DataFrame(
        [row.readings for row in request.history],
        index=pd.DatetimeIndex(
            [row.timestamp for row in request.history], name="Datetime",
        ),
    ).asfreq("30min")


@router.post(
    "/predict-multistation",
    response_model=MultiStationPredictResponse,
    tags=["prediction"],
)
async def predict_multistation(
    request: MultiStationPredictRequest,
    http_request: Request,
) -> MultiStationPredictResponse:
    """Prediksi h1–h6 untuk 15 stasiun dengan skenario atau ensemble hujan."""
    try:
        predictor = get_hybrid_predictor(http_request)
        history = _history_to_dataframe(request)
        if request.future_rainfall is None:
            result = predictor.predict_ensemble_from_history(
                history,
                scenario_count=request.scenario_count,
                seed=request.seed,
            )
            scenario_spread = result.scenario_spread
        else:
            rainfall = pd.Series(request.future_rainfall, dtype=float)
            result = predictor.predict_from_history(
                history,
                future_rainfall=rainfall,
            )
            scenario_spread = None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail=f"Hybrid model unavailable: {exc}",
        ) from exc

    return MultiStationPredictResponse(
        predictions=result.predictions,
        simulator_predictions=result.simulator_predictions,
        scenario_spread=scenario_spread,
        backend=predictor.backend_name,
        models=predictor.model_mapping(),
        timestamp=request.history[-1].timestamp,
        prediction_time=datetime.now(timezone.utc),
        scenario_count=getattr(result, "scenario_count", 1),
        future_rainfall_mode=result.future_rainfall_mode,
    )
