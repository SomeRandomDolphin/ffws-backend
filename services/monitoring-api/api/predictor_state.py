from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from dhompo.config import load_serving_config
from dhompo.serving.file_predictor import BEST_MODEL_FILES, FilePredictor
from dhompo.serving.two_tier import TwoTierPredictor

logger = logging.getLogger(__name__)
_SERVING_CFG = load_serving_config()


def configured_backend() -> str:
    return os.getenv("PREDICTOR_BACKEND", "file").strip().lower()


def mlflow_tracking_uri() -> str:
    return os.getenv(
        "MLFLOW_TRACKING_URI", _SERVING_CFG.get("mlflow_uri", "http://localhost:5000")
    )


def model_alias() -> str:
    return os.getenv("MODEL_ALIAS", _SERVING_CFG.get("model_alias", "production"))


def build_model_references(backend: str | None = None) -> dict[str, str]:
    backend_name = backend or configured_backend()
    if backend_name == "mlflow":
        alias = model_alias()
        return {f"h{h}": f"models:/dhompo_h{h}@{alias}" for h in range(1, 6)}
    if backend_name == "tier_a_adaptive":
        return {f"h{h}": "tier_a_adaptive" for h in range(1, 6)}
    return {f"h{h}": fname for h, fname in BEST_MODEL_FILES.items()}


def create_predictor() -> Any:
    backend = configured_backend()
    if backend == "mlflow":
        try:
            from dhompo.serving.predictor import HistoricalPredictor
        except ImportError as exc:
            raise RuntimeError(
                "MLflow backend requested but MLflow dependencies are not available "
                "in this environment."
            ) from exc

        return HistoricalPredictor(
            mlflow_tracking_uri=mlflow_tracking_uri(),
            alias=model_alias(),
        )

    if backend == "file":
        return TwoTierPredictor(tier_a=FilePredictor())

    if backend == "tier_a_adaptive":
        try:
            from dhompo.serving.tier_a_adaptive import TierAAdaptivePredictor
        except ImportError as exc:
            raise RuntimeError(
                "Tier-A adaptive backend requested but PyTorch is not installed. "
                "Run: pip install -r requirements-torch.txt"
            ) from exc
        return TwoTierPredictor(tier_a=TierAAdaptivePredictor())

    raise RuntimeError(f"Unsupported predictor backend: {backend}")


def load_predictor_state(app: FastAPI) -> None:
    backend = configured_backend()
    app.state.predictor_backend = backend
    app.state.predictor = None
    app.state.predictor_error = None
    app.state.hybrid_predictor = None
    app.state.hybrid_predictor_error = None

    try:
        predictor = create_predictor()
    except Exception as exc:
        app.state.predictor_error = str(exc)
        logger.exception("Predictor startup failed for backend '%s'.", backend)
    else:
        app.state.predictor = predictor
        logger.info("Predictor ready for backend '%s'.", backend)

    try:
        from dhompo.serving.hybrid_predictor import HybridMultiStationPredictor

        app.state.hybrid_predictor = HybridMultiStationPredictor()
    except Exception as exc:
        app.state.hybrid_predictor_error = str(exc)
        logger.warning("Hybrid multi-station predictor unavailable: %s", exc)


def get_predictor(request: Request) -> Any:
    predictor = getattr(request.app.state, "predictor", None)
    if predictor is None:
        error = getattr(request.app.state, "predictor_error", None)
        detail = "Predictor is not ready."
        if error:
            detail = f"Predictor is not ready: {error}"
        raise HTTPException(status_code=503, detail=detail)
    return predictor


def get_hybrid_predictor(request: Request) -> Any:
    predictor = getattr(request.app.state, "hybrid_predictor", None)
    if predictor is None:
        error = getattr(request.app.state, "hybrid_predictor_error", None)
        detail = "Hybrid multi-station predictor is not ready."
        if error:
            detail = f"Hybrid multi-station predictor is not ready: {error}"
        raise HTTPException(status_code=503, detail=detail)
    return predictor


def readiness_status(app: FastAPI) -> dict[str, Any]:
    backend = getattr(app.state, "predictor_backend", configured_backend())
    predictor = getattr(app.state, "predictor", None)
    error = getattr(app.state, "predictor_error", None)
    ready = predictor is not None and error is None
    models = (
        predictor.model_mapping()
        if ready and hasattr(predictor, "model_mapping")
        else build_model_references(backend)
    )
    return {
        "status": "ok" if ready else "error",
        "ready": ready,
        "backend": backend,
        "models": models,
        "error": error,
    }
