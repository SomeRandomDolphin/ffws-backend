"""Monitoring entrypoint; prediction imports are optional and lazy."""
import os
import sys
from pathlib import Path
from contextlib import asynccontextmanager

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi import FastAPI, HTTPException
from monitoring.api import router, database

@asynccontextmanager
async def lifespan(app):
    if os.getenv("PREDICTIONS_ENABLED", "false").lower() == "true":
        from api.predictor_state import load_predictor_state
        from api.routes.predict import router as predict
        from api.routes.multistation import router as multi
        from api.routes.health import router as model_health
        load_predictor_state(app)
        app.include_router(predict)
        app.include_router(multi)
        # Preserve model-info; monitoring readiness is independent of the models.
        for route in model_health.routes:
            if route.path == "/model-info":
                app.router.routes.append(route)
    yield
    database().dispose()

app = FastAPI(title="FFWS Monitoring API", lifespan=lifespan)
app.include_router(router)

@app.get("/")
def root():
    return {"service": "FFWS Monitoring API", "docs": "/docs"}

if os.getenv("PREDICTIONS_ENABLED", "false").lower() != "true":
    @app.post("/predict")
    @app.post("/predict-multistation")
    def predictions_disabled():
        raise HTTPException(503, "Prediksi belum diaktifkan")

    @app.get("/model-info")
    def model_info():
        return {"enabled": False, "ready": False, "reason": "PREDICTIONS_ENABLED=false"}
