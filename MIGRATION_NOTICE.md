# Migration Notice: History & Prediction Endpoints

**Audience:** frontend team consuming `/getHistory`, `/getHistoryPrediction`, `/getChartData`.

**Why:** the ML prediction backend changed from a single Dhompo/Purwodadi model set (LSTM/GRU/TCN, hourly, Flask) to a new multi-horizon FastAPI service. That forced changes to the request/response contracts on all three endpoints below. Please read the **Breaking Changes** section for each endpoint before updating client code.

---

## `GET /getHistory`

### What changed
- `daerah` now only accepts the **13 canonical water-level stations** used by the prediction model, instead of `{lawang, cendono, purwodadi, dhompo}`.
- **Rainfall is no longer available.** `daerah=lawang` and `daerah=cendono` (previously rainfall readings) now fall into the "unrecognized daerah" case.

### ⚠️ Breaking change
`daerah=lawang` / `daerah=cendono` return an **empty result**, not an error:
```json
{
  "statusCode": 200,
  "status": true,
  "message": "Get All History successfully",
  "data": { "history": null, "total_count": 0 }
}
```
If your UI has a rainfall view backed by this endpoint, it will silently show no data. This needs a product decision, not just a code fix, if rainfall history is still needed somewhere.

### Valid `daerah` values now
`dhompo`, `purwodadi`, `bd_suwoto`, `krajan_timur`, `bd_lecari`, `bd_bakalan`, `bd_baong`, `awlr_kademungan`, `bd_guyangan`, `sidogiri`, `bd_domas`, `klosod`, `bd_grinting`

(Human-readable forms like `Bd. Suwoto`, `bd-suwoto`, or `BD SUWOTO` are also accepted and normalized server-side.)

### Response shape — unchanged
```json
{
  "data": {
    "history": [
      { "id": 3135, "dhompo": 9.145, "tanggal": "2022-12-05 15:00:00" }
    ],
    "total_count": 3135
  }
}
```
The value key still matches whatever `daerah` you requested (e.g. `"dhompo"`, `"purwodadi"`) — only the *set* of valid keys changed.

---

## `GET /getHistoryPrediction`

### What changed
- **New optional `daerah` query param** (didn't exist before — there was only ever one predicted station). Omit it to get predictions across all currently-predicted stations; pass it to filter to one.
- **Entirely new response shape.** The old 6-column format (`purwodadi`/`dhompo` × `lstm`/`gru`/`tcn`, keyed by `predicted_for_time`) is gone.

### ⚠️ Breaking change — full response shape rewrite

**Before:**
```json
{
  "prediksi_level_muka_air_dhompo_lstm": 1.42,
  "prediksi_level_muka_air_dhompo_gru": 1.38,
  "prediksi_level_muka_air_dhompo_tcn": 1.45,
  "prediksi_level_muka_air_purwodadi_lstm": null,
  "status_muka_air": "AMAN",
  "predicted_from_time": "2022-11-21T10:00:00",
  "predicted_for_time": "2022-11-21T11:00:00"
}
```

**After:**
```json
{
  "id": 1,
  "daerah": "dhompo",
  "source_timestamp": "2022-12-05 15:00:00",
  "prediction_time": "2026-07-07 05:00:27",
  "backend": "tier_a_adaptive",
  "serving_tier": "A",
  "models": {
    "h1": "tier_a_adaptive", "h2": "tier_a_adaptive", "h3": "tier_a_adaptive",
    "h4": "tier_a_adaptive", "h5": "tier_a_adaptive"
  },
  "predictions": { "h1": 8.917, "h2": 8.988, "h3": 8.923, "h4": 9.015, "h5": 9.101 },
  "shadow_predictions": { "h1": 9.145, "h2": 9.145, "h3": 9.145, "h4": 9.145, "h5": 9.145 },
  "status": { "h1": "AMAN", "h2": "AMAN", "h3": "AMAN", "h4": "AMAN", "h5": "AMAN" },
  "degradation": {},
  "quality_flags": { "Bd. Suwoto": "OK", "...": "OK", "Dhompo": "OK" }
}
```

### Key differences to design around
| Concept | Before | After |
|---|---|---|
| Which station | Both Dhompo and Purwodadi, always | Whichever `daerah` was predicted — only `dhompo` today |
| Which model | 3 models shown per station (`lstm`/`gru`/`tcn`) | 1 model per horizon, named in `models.h1`–`h5` |
| Time horizon | Single target time (`predicted_for_time`) | 5 horizons per row: `predictions.h1` (+1h) through `h5` (+5h), all relative to `source_timestamp` |
| Status | One `status_muka_air` for the row | One `status` **per horizon** (`status.h1`–`h5`) |
| New fields | — | `serving_tier` (`"A"` normal / `"B"` fallback), `degradation` (per-horizon reason if a primary sensor is bad), `shadow_predictions` (fallback model's prediction, for comparison), `quality_flags` (per-station sensor health) |

If your UI currently renders "the next hour's prediction" as one number, that maps to `predictions.h1`. If it shows a multi-hour forecast, `h1`–`h5` gives you that natively now — no need for multiple API calls.

### Known cosmetic quirk
`degradation` may render as `[]` instead of `{}` when empty (PHP can't distinguish an empty array from an empty object). Treat both as "no degradation" — don't rely on strict type checks here.

---

## `GET /getChartData`

### What changed
- **`model` parameter removed.** The new ML API selects one model per horizon server-side; there's nothing left for a client to choose.
- **`daerah` kept**, but only `dhompo` is valid today (more stations will be added as their models go live — this is a config change on the backend, watch for announcements rather than assuming a station works).
- **`periode` capped at 5.** A single prediction run only ever forecasts 5 hours ahead, so `periode` values above 5 are silently clamped to 5. The old system could sometimes show further out via accumulated hourly runs — that's no longer possible with the new one-shot 5-horizon model.

### ⚠️ Breaking change
Requesting an unsupported `daerah` (e.g. `purwodadi`, until its model is live) now returns a **404**, not data:
```json
{
  "statusCode": 404,
  "status": false,
  "message": "Prediction chart is not yet available for 'purwodadi'. Supported: dhompo.",
  "data": null
}
```

### Response shape — unchanged
Still a flat, timeline-joined array:
```json
[
  { "aktual": 9.273, "prediksi": null, "tanggal": "2022-12-05 05:00:00" },
  { "aktual": 9.145, "prediksi": null, "tanggal": "2022-12-05 15:00:00" },
  { "aktual": null, "prediksi": 8.917, "tanggal": "2022-12-05 16:00:00" }
]
```
- Rows with `aktual` set and `prediksi: null` are historical readings with no matching past prediction *yet* (this fills in over time as more prediction runs accumulate — it's not a bug if it's sparse on a freshly-deployed system).
- Rows with `aktual: null` and `prediksi` set are the future forecast — capped at `periode` entries (max 5).

---

## Summary checklist for frontend work

- [ ] Remove any UI/logic depending on `daerah=lawang` or `daerah=cendono` rainfall data from `/getHistory`, or flag to backend that rainfall history needs to be re-added elsewhere.
- [ ] Rebuild whatever consumes `/getHistoryPrediction` against the new nested shape (`predictions.h1`–`h5`, not flat model×station columns).
- [ ] Remove any `model=` selector UI for `/getChartData` — it no longer does anything.
- [ ] Handle the new `404` response from `/getChartData` for unsupported `daerah` gracefully (e.g. "forecast not yet available for this station" rather than a generic error).
- [ ] Decide how to surface (or ignore) the new `serving_tier`, `degradation`, `shadow_predictions`, and `quality_flags` fields — none of this existed in the old contract, so it's currently unused by any UI.
- [ ] Confirm with backend which `daerah` are live before wiring up a picker — only `dhompo` today.