# FFWS Backend

FastAPI service for water-level forecasting in the Dhompo/Welang River Basin and monitoring data in Surabaya. The service exposes model readiness, single-station forecasts, multi-station forecasts, and the latest Surabaya monitoring snapshot for the FFWS frontend.

## Main Endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/health` | API and predictor readiness |
| `GET` | `/model-info` | Active backend, model references, and input requirements |
| `POST` | `/predict` | Dhompo water-level forecasts for the next 1 to 5 hours |
| `POST` | `/predict-multistation` | Hybrid forecasts for 15 Welang monitoring stations |
| `GET` | `/surabaya/snapshot` | Latest cached Surabaya readings and forecasts |

Interactive API documentation is available at `/docs` and `/redoc` while the service is running.

## Requirements

- Python 3.10 or newer
- [`uv`](https://docs.astral.sh/uv/) or `pip`
- MySQL access only when live Surabaya ingestion is enabled

## Local Development

Install the locked development environment:

```bash
uv sync --extra dev
```

Copy the environment template:

```bash
cp .env.example .env
```

On Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

Start the API:

```bash
uv run uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000/docs` to inspect and call the endpoints.

## Predictor Configuration

The default `file` backend loads the versioned model artifacts under `models/`. The following environment variables control model serving:

| Variable | Default | Description |
|---|---|---|
| `PREDICTOR_BACKEND` | `file` | Predictor implementation: `file` or `mlflow` |
| `MLFLOW_TRACKING_URI` | value in `configs/dhompo/serving.yaml` | MLflow tracking URI when the MLflow backend is selected |
| `MODEL_ALIAS` | `production` | MLflow model alias |

The optional adaptive Tier-A backend requires the additional dependencies in `requirements-torch.txt`.

### Model artifact compatibility

The repository includes the five baseline Dhompo model files retained from the research repository. A deployment must verify that these pickle files are compatible with its Python and library versions and provide the matching `scaler.pkl` where required. If a model cannot be loaded, the API still starts, `/health` returns `503`, and prediction endpoints remain unavailable until compatible artifacts are supplied.

## Surabaya Data Source

The Surabaya worker reads MySQL in read-only mode and keeps a local SQLite cache at `artifacts/surabaya/live.sqlite3`. Without database credentials, the API remains available and the snapshot endpoint reports that its live source is unavailable.

Create the local configuration from the supplied example:

```powershell
Copy-Item configs/surabaya/database.example.yaml configs/surabaya/database.local.yaml
```

Fill in the local database settings. `database.local.yaml` is excluded from version control and must not be committed.

## Tests

```bash
uv run pytest
```

## Docker

Build and run the service directly:

```bash
docker build -t ffws-backend .
docker run --rm -p 8000:8000 --env-file .env ffws-backend
```

For Docker Compose, first create `.env` and `configs/surabaya/database.local.yaml`, then run:

```bash
docker compose up --build
```

## Related Repositories

- FFWS Frontend: https://github.com/SomeRandomDolphin/ffws-frontend
- FFWS Backend: https://github.com/SomeRandomDolphin/ffws-backend
- FFWS Machine Learning: https://github.com/SomeRandomDolphin/ffws-ml

The frontend consumes REST APIs from this service, while this backend integrates forecasting models and hydrological data for monitoring and analytics.

## Project Affiliation and Intended Use

This project is funded through a research program at Institut Teknologi Sepuluh Nopember (ITS) and developed by the Department of Civil Engineering. It is intended for government and research use under the Flood Forecasting Warning System initiative.
