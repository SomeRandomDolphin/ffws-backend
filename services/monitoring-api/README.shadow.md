# Surabaya shadow forecasts

Untuk pengoperasian Supabase dan perbedaan data agregat versus sumber mentah, lihat [README.central.md](README.central.md).

Internal experiment only; no change to public station forecasts or flood alerts. Both persistence and damped trend (tau=1h) run as fixed, versioned baselines. This is not deployment of an approved model selected per station.

Enable SHADOW_FORECAST_ENABLED=true on the monitoring worker and API. Run python -m monitoring.worker init once to create additive shadow_runs/shadow_forecasts tables in the dedicated archive; existing observations remain unchanged. Single worker instance only. Run python -m monitoring.worker run. Container deployments must give the worker the same flag and database as the API; default remains disabled. Disable flag to stop issuing; preserve tables for audit.

Independent thread checks every 60 seconds. Issues only during first 10 minutes after the closed hourly origin; no historical catch-up. issued_at records actual publication time. Horizon targets are origin+1..5h, not issued_at+1..5h. Inputs are last 72 complete five-minute bins; each needs >=5 unique timestamps and no invalid samples. Values zero, negative, above configured reference, conflicting duplicates, or constant positive >=6h are excluded causally. No interpolation. Only rows received before issue are used. Store immutable input water levels, source max ID, reference, version, tau, origin and issue time. Skipped runs store a reason. ID makes retries idempotent; concurrent worker processes are unsupported.

Score the five-minute bin ending at each target, using same QC and receipt cutoff. Begin after 10 minutes, retry pending targets for up to 24h, then mark unavailable. First accepted valid target is immutable; later source corrections do not rewrite metrics. This defines a 24h verification latency and excludes invalid targets: report issued/scored/pending/unavailable beside scores. Differences from offline evaluation: actual transmission latency, fixed tau across channels, and rolling QC.

GET /v1/shadow/metrics returns per-sensor/model/horizon sample counts, MAE/RMSE/bias in cm, plus issued/skipped run counts. Endpoint is disabled unless flag true; existing monitoring API has no authentication, so keep this service private or protect it at gateway before network exposure. Metrics currently all-time; model version is surabaya-baselines-v1. Back up archive DB including shadow tables. Tables grow additively; no automatic deletion.

No calibration changes, cross-station imputation, calibrated uncertainty intervals, or operational safety claims. Inspect skip rates and scored coverage before interpreting errors. Minimum 30 origins and 3 different days is an initial reporting threshold, not proof of flood-event performance. Use shadow_runs origin/station/sensor and shadow_forecasts run_id to export evaluation datasets.

Tests: python -m pytest monitoring_tests/test_shadow.py -q

Kalibokor issuance is paused (insufficient validation samples); prior forecasts remain available for scoring and audit. Other three stations remain eligible subject to input QC.

Drawer preview uses /v1/stations/{station}/shadow to compute from the six hours ending at the latest complete 5-minute bin before last available observation. Target times are anchored to that historical origin, never shifted to the current clock. Returned mode=last_available_preview is NOT inserted into shadow metrics; historical flag is true when the first target is in the past. Operational hourly publication/evaluation continues independently. Kalibokor remains excluded.

Preview computations are now persisted immutably in shadow_preview_history, deduplicated by station/sensor/origin/model version and input values/reference hash. Repeated polling does not duplicate records; corrected input creates a separate snapshot. Replay records never enter operational MAE/RMSE. Range Excel includes a Prediksi sheet containing both models, target/origin/computation times in WIB, version, type, status, and IDs. Date range filters target time, not calculation time. History starts at activation; absent past forecasts are not invented. Preview snapshots are saved when requested; scheduled forecasts continue automatically for eligible channels. Run worker init after updating schema.
