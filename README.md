# FFWS Backend

FastAPI service for the Flood Forecasting Warning System (FFWS). The API serves water-level predictions for the Dhompo and Welang River Basin and exposes cached monitoring data for Surabaya.

## What the API Provides

- Dhompo water-level predictions for horizons `h1` to `h5`
- Hybrid predictions for 15 Welang monitoring stations for horizons `h1` to `h6`
- Model readiness and configuration information
- Cached Surabaya water-level, rainfall, telemetry, and forecast data
- OpenAPI documentation through Swagger UI and ReDoc

## API Reference

| Method | Endpoint | Description |
| --- | --- | --- |
| `GET` | `/` | Service information and documentation link |
| `GET` | `/health` | Predictor readiness and loaded model references |
| `GET` | `/model-info` | Backend, model requirements, and hybrid predictor status |
| `POST` | `/predict` | Dhompo forecast for the next 1 to 5 hours |
| `POST` | `/predict-multistation` | Welang multi-station forecast for the next 1 to 6 hours |
| `GET` | `/surabaya/snapshot` | Latest cached Surabaya readings and forecast status |

When the API is running, open [`/docs`](http://localhost:8000/docs) for the interactive reference or [`/redoc`](http://localhost:8000/redoc) for the alternative documentation view.

## Project Layout

```text
api/                    FastAPI application and route handlers
configs/                Serving, station, and Surabaya source configuration
models/                 Versioned baseline model files and model notes
src/dhompo/             Prediction, data, and serving modules
tests/                  API and predictor tests
Dockerfile              Production container image
docker-compose.yml      Local container orchestration
payload.json            Example request payload for /predict
```

## Requirements

- Python 3.10 or newer
- [`uv`](https://docs.astral.sh/uv/) or `pip`
- Docker, if you use the container setup
- MySQL access only when live Surabaya ingestion is enabled

## Local Setup

Install the locked development environment:

```bash
uv sync --extra dev
```

Create a local environment file:

```bash
cp .env.example .env
```

PowerShell:

```powershell
Copy-Item .env.example .env
```

Start the development server:

```bash
uv run uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```

The service is available at `http://localhost:8000`.

## Predictor Backends

The API selects its predictor through environment variables.

| Variable | Default | Description |
| --- | --- | --- |
| `PREDICTOR_BACKEND` | `file` | Selects the local file backend or `mlflow` |
| `MLFLOW_TRACKING_URI` | `mlruns` from `configs/dhompo/serving.yaml` | MLflow tracking server used by the MLflow backend |
| `MODEL_ALIAS` | `production` | Model alias used by the MLflow backend |

The optional adaptive Tier-A backend requires the packages listed in `requirements-torch.txt` and the corresponding adaptive model artifacts.

### Model files

The repository includes the five baseline Dhompo model files retained from the research repository. Check that the pickle files match the Python and library versions used at deployment time. The linear models also require a matching `scaler.pkl` from the same training run.

If a model cannot be loaded, the API still starts so that operational endpoints remain inspectable. In that state, `/health` returns `503` and prediction routes report that the predictor is unavailable.

## Surabaya Monitoring

The Surabaya worker reads configured MySQL sources in read-only mode and stores a local SQLite cache at `artifacts/surabaya/live.sqlite3`. The API can start without database credentials. In that case, `/surabaya/snapshot` returns the available cached state and reports the source as unavailable.

Create the local database configuration from the supplied template:

```powershell
Copy-Item configs/surabaya/database.example.yaml configs/surabaya/database.local.yaml
```

Edit the local file with the database connection values. Git ignores `database.local.yaml`; do not commit it.

## Docker

Build and run the API image:

```bash
docker build -t ffws-backend .
docker run --rm -p 8000:8000 --env-file .env ffws-backend
```

For Compose, create `.env` and `configs/surabaya/database.local.yaml` first:

```bash
docker compose up --build
```

The API listens on port `8000`.

## Testing

Run the full test suite:

```bash
uv run pytest
```

Tests that require model artifacts will only pass when compatible model files and their matching preprocessing artifacts are available.

## Related Repositories

- [FFWS Frontend](https://github.com/SomeRandomDolphin/ffws-frontend)
- [FFWS Backend](https://github.com/SomeRandomDolphin/ffws-backend)
- [FFWS Machine Learning](https://github.com/SomeRandomDolphin/ffws-ml)

The frontend consumes REST APIs from this backend. The backend connects hydrological data sources with forecasting models for monitoring and analysis.

## Project Affiliation and Intended Use

This project is funded through a research program at Institut Teknologi Sepuluh Nopember (ITS) and developed by the Department of Civil Engineering. It is intended for government and research use under the Flood Forecasting Warning System initiative.
