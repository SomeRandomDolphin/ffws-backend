from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from api.predictor_state import build_model_references, model_alias, readiness_status
from dhompo.data.loader import UPSTREAM_STATIONS, TARGET_STATION
from dhompo.serving.file_predictor import SCALER_FILENAME

router = APIRouter()


@router.get("/health", tags=["health"])
async def health_check(request: Request) -> JSONResponse:
    """Return service readiness status."""
    payload = readiness_status(request.app)
    payload["timestamp"] = datetime.now(timezone.utc)
    status_code = 200 if payload["ready"] else 503
    return JSONResponse(status_code=status_code, content=jsonable_encoder(payload))


@router.get("/model-info", tags=["health"])
async def model_info(request: Request) -> dict:
    """Return info about loaded models and required input shape."""
    readiness = readiness_status(request.app)
    backend = readiness["backend"]
    model_refs = readiness["models"] or build_model_references(backend)
    return {
        "backend": backend,
        "available_backends": ["file", "mlflow"],
        "models": model_refs,
        "scaler": SCALER_FILENAME,
        "required_history_rows": 24,
        "required_stations": UPSTREAM_STATIONS + [TARGET_STATION],
        "ready": readiness["ready"],
        "error": readiness["error"],
        "model_alias": model_alias() if backend == "mlflow" else None,
        "hybrid_multistation_ready": (
            getattr(request.app.state, "hybrid_predictor", None) is not None
        ),
        "hybrid_multistation_error": getattr(
            request.app.state, "hybrid_predictor_error", None,
        ),
        "hybrid_required_history_rows": 48,
        "hybrid_required_stations": 15,
        "hybrid_horizons": [1, 2, 3, 4, 5, 6],
    }
