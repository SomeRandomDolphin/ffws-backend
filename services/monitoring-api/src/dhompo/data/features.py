"""Feature engineering for Dhompo multi-horizon flood forecasting.

Ported from research/create_02_modeling.py — direct multi-step strategy:
build one feature matrix at time t, then shift target by h steps.

Base features: ~160
  t0 values     : 13
  lag 1-3       : 39  (or travel-time-based lags when enabled)
  rolling mean  : 39 (3h/6h/12h per station)
  rolling std   : 39 (3h/6h/12h per station)
  rate of change: 26 (diff1/diff2 per station)
  temporal      :  4 (hour_sin, hour_cos, dayofweek, is_night)

Enhanced features (optional):
  cumulative rainfall : 4 (rolling sum 3h/6h/12h/24h)
  interaction         : upstream gradient + rainfall×gradient
  seasonal/AMI        : wet_season flag + 7-day antecedent moisture index
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .loader import ALL_STATIONS, UPSTREAM_STATIONS, TARGET_STATION, STATION_META, DataSegment


# Empirical travel time from EDA (xls_06): lag in 30-min steps per station.
# Stations closer to Dhompo have shorter travel times.
TRAVEL_TIME_LAGS: dict[str, list[int]] = {
    "Bd. Suwoto":      [7, 9, 11],     # 210–330 min
    "Krajan Timur":    [5, 7, 9],      # 150–270 min
    "Purwodadi":       [4, 6, 8],      # 120–240 min
    "Bd. Baong":       [3, 5, 7],      # 90–210 min
    "Bd. Lecari":      [3, 4, 6],      # 90–180 min
    "Bd. Bakalan":     [3, 4, 5],      # 90–150 min
    "AWLR Kademungan": [3, 4, 5],      # 90–150 min
    "Bd. Domas":       [2, 3, 4],      # 60–120 min
    "Bd Guyangan":     [1, 2, 3],      # 30–90 min
    "Bd. Grinting":    [1, 2, 3],      # 30–90 min
    "Sidogiri":        [1, 2, 3],      # 30–90 min
    "Klosod":          [1, 2, 3],      # 30–90 min
    "Dhompo":          [1, 2, 3],      # target self-lags
}


def build_forecast_features(
    df: pd.DataFrame,
    upstream_stations: list[str] | None = None,
    target: str = TARGET_STATION,
    extra_columns: list[str] | None = None,
    use_travel_time_lags: bool = False,
    use_cumulative_rainfall: bool = False,
    use_interaction_features: bool = False,
    use_seasonal_features: bool = False,
) -> pd.DataFrame:
    """Build feature matrix at time t for predicting target(t+h).

    Parameters
    ----------
    df:
        Raw sensor DataFrame with DatetimeIndex.
    upstream_stations:
        List of upstream station column names. Defaults to the canonical list.
    target:
        Name of the target station column.
    extra_columns:
        Additional columns (e.g. ``["Curah hujan"]``) to include as features.
        For each extra column, generates: t0, lag1-3, rolling mean/std
        3h/6h/12h, diff1/diff2 (~13 features per column).
    use_travel_time_lags:
        If True, use empirical travel-time-based lags per station instead
        of uniform lag 1-3 for all stations (B1).
    use_cumulative_rainfall:
        If True, add rolling sum rainfall features for 3h/6h/12h/24h (B2).
        Requires "Curah hujan" in extra_columns or df.columns.
    use_interaction_features:
        If True, add upstream gradient and gradient×rainfall features (B3).
    use_seasonal_features:
        If True, add wet_season flag and 7-day antecedent moisture index (B4).

    Returns
    -------
    pd.DataFrame
        Feature matrix with NaN rows dropped (first ~24 rows removed due to lag/rolling).
    """
    if upstream_stations is None:
        upstream_stations = UPSTREAM_STATIONS

    all_stations = upstream_stations + [target]
    cols: dict[str, pd.Series] = {}

    # 1. Current value + lags (travel-time-based or fixed)
    for st in all_stations:
        cols[f"{st}_t0"] = df[st]
        if use_travel_time_lags and st in TRAVEL_TIME_LAGS:
            for lag in TRAVEL_TIME_LAGS[st]:
                cols[f"{st}_lag{lag}"] = df[st].shift(lag)
        else:
            for lag in range(1, 4):
                cols[f"{st}_lag{lag}"] = df[st].shift(lag)

    # 2. Rolling mean & std (window in 30-min steps → 3h=6, 6h=12, 12h=24)
    for st in all_stations:
        for window, label in [(6, "3h"), (12, "6h"), (24, "12h")]:
            roll = df[st].rolling(window)
            cols[f"{st}_rmean_{label}"] = roll.mean()
            cols[f"{st}_rstd_{label}"] = roll.std()

    # 3. Rate of change
    for st in all_stations:
        cols[f"{st}_diff1"] = df[st].diff(1)
        cols[f"{st}_diff2"] = df[st].diff(2)

    # 4. Temporal features (cyclical encoding)
    hour = df.index.hour + df.index.minute / 60.0
    cols["hour_sin"] = pd.Series(np.sin(2 * np.pi * hour / 24), index=df.index)
    cols["hour_cos"] = pd.Series(np.cos(2 * np.pi * hour / 24), index=df.index)
    cols["dayofweek"] = pd.Series(df.index.dayofweek.astype(float), index=df.index)
    cols["is_night"] = pd.Series(
        ((df.index.hour >= 19) | (df.index.hour < 6)).astype(float), index=df.index
    )

    # 5. Extra columns (e.g. rainfall)
    rainfall_col = None
    if extra_columns:
        for col in extra_columns:
            if col not in df.columns:
                continue
            cols[f"{col}_t0"] = df[col]
            for lag in range(1, 4):
                cols[f"{col}_lag{lag}"] = df[col].shift(lag)
            for window, label in [(6, "3h"), (12, "6h"), (24, "12h")]:
                roll = df[col].rolling(window)
                cols[f"{col}_rmean_{label}"] = roll.mean()
                cols[f"{col}_rstd_{label}"] = roll.std()
            cols[f"{col}_diff1"] = df[col].diff(1)
            cols[f"{col}_diff2"] = df[col].diff(2)
            if col == "Curah hujan":
                rainfall_col = col

    # 6. Cumulative rainfall features (B2)
    if use_cumulative_rainfall:
        rcol = rainfall_col or ("Curah hujan" if "Curah hujan" in df.columns else None)
        if rcol and rcol in df.columns:
            for window, label in [(6, "3h"), (12, "6h"), (24, "12h"), (48, "24h")]:
                cols[f"rainfall_cumsum_{label}"] = df[rcol].rolling(window, min_periods=1).sum()

    # 7. Interaction features (B3)
    if use_interaction_features:
        # Hydraulic gradient: difference between adjacent upstream stations
        sorted_upstream = sorted(
            [s for s in upstream_stations if s in STATION_META],
            key=lambda s: STATION_META[s][0],
            reverse=True,
        )
        for i in range(len(sorted_upstream) - 1):
            hi, lo = sorted_upstream[i], sorted_upstream[i + 1]
            grad_name = f"gradient_{hi}_{lo}"
            cols[grad_name] = df[hi] - df[lo]

        # Upstream mean minus target (overall gradient proxy)
        upstream_mean = df[upstream_stations].mean(axis=1)
        cols["upstream_mean_diff"] = upstream_mean - df[target]

        # Gradient × cumulative rainfall interaction
        rcol = rainfall_col or ("Curah hujan" if "Curah hujan" in df.columns else None)
        if rcol and rcol in df.columns:
            rain_6h = df[rcol].rolling(12, min_periods=1).sum()
            cols["gradient_x_rain6h"] = cols["upstream_mean_diff"] * rain_6h

    # 8. Seasonal / antecedent moisture features (B4)
    if use_seasonal_features:
        # Wet season flag (Nov–Apr for East Java)
        month = df.index.month
        cols["wet_season"] = pd.Series(
            ((month >= 11) | (month <= 4)).astype(float), index=df.index
        )
        # Antecedent moisture index: rolling sum of rainfall over 7 days
        rcol = rainfall_col or ("Curah hujan" if "Curah hujan" in df.columns else None)
        if rcol and rcol in df.columns:
            # 7 days = 336 steps at 30-min interval
            cols["ami_7d"] = df[rcol].rolling(336, min_periods=1).sum()

    feat = pd.concat(cols, axis=1)
    feat = feat.dropna()
    return feat


def build_features_from_segments(
    segments: list[DataSegment],
    upstream_stations: list[str] | None = None,
    target: str = TARGET_STATION,
    extra_columns: list[str] | None = None,
    use_travel_time_lags: bool = False,
    use_cumulative_rainfall: bool = False,
    use_interaction_features: bool = False,
    use_seasonal_features: bool = False,
) -> pd.DataFrame:
    """Build features per segment separately, then concatenate.

    This prevents lag/rolling features from being contaminated by gaps
    between time periods (e.g. the ~26-day gap between Dec 2022 and Jan 2023).

    Parameters
    ----------
    segments:
        List of DataSegment from ``load_combined_data()``.
    upstream_stations:
        Upstream station column names.
    target:
        Target station name.
    extra_columns:
        Additional feature columns (e.g. ``["Curah hujan"]``).
    use_travel_time_lags:
        Use empirical travel-time-based lags per station.
    use_cumulative_rainfall:
        Add cumulative rainfall rolling sum features.
    use_interaction_features:
        Add upstream gradient and interaction features.
    use_seasonal_features:
        Add wet_season and antecedent moisture index features.

    Returns
    -------
    pd.DataFrame
        Concatenated feature matrix from all segments.
    """
    parts = []
    for seg in segments:
        df = seg.df.copy()
        # Attach extra columns (e.g. rainfall) to the segment DataFrame
        if extra_columns:
            for col in extra_columns:
                if seg.rainfall is not None and col == seg.rainfall.name:
                    df[col] = seg.rainfall
                elif col not in df.columns:
                    # Fill with 0 for segments without the extra column
                    # (e.g. 2022 has no rainfall data → assume dry)
                    df[col] = 0.0
        feat = build_forecast_features(
            df,
            upstream_stations=upstream_stations,
            target=target,
            extra_columns=extra_columns,
            use_travel_time_lags=use_travel_time_lags,
            use_cumulative_rainfall=use_cumulative_rainfall,
            use_interaction_features=use_interaction_features,
            use_seasonal_features=use_seasonal_features,
        )
        parts.append(feat)
    return pd.concat(parts, axis=0)


def build_targets(
    df: pd.DataFrame,
    horizons: list[int],
    horizon_steps: dict[int, int],
    target: str = TARGET_STATION,
) -> dict[int, pd.Series]:
    """Build target series for each horizon.

    Parameters
    ----------
    df:
        Raw sensor DataFrame.
    horizons:
        List of forecast horizons in hours, e.g. [1, 2, 3, 4, 5].
    horizon_steps:
        Mapping from horizon → number of 30-min steps, e.g. {1: 2, 2: 4, ...}.
    target:
        Name of the target column.

    Returns
    -------
    dict mapping horizon (int) → pd.Series of future target values.
    """
    return {h: df[target].shift(-horizon_steps[h]) for h in horizons}


def align_features_targets(
    X: pd.DataFrame,
    y_horizons: dict[int, pd.Series],
) -> tuple[pd.DataFrame, dict[int, pd.Series]]:
    """Intersect valid indices across all horizons and the feature matrix.

    Returns aligned (X, y_horizons) with no NaN rows.
    """
    valid_idx = X.index
    for h, y in y_horizons.items():
        valid_idx = valid_idx.intersection(y.dropna().index)

    X_aligned = X.loc[valid_idx]
    y_aligned = {h: y.loc[valid_idx] for h, y in y_horizons.items()}
    return X_aligned, y_aligned


def build_multistation_features(
    df: pd.DataFrame,
    stations: list[str] | None = None,
    extra_columns: list[str] | None = None,
) -> pd.DataFrame:
    """Bangun fitur pada waktu t dari seluruh stasiun untuk model multi-output."""
    station_names = list(stations or ALL_STATIONS)
    missing = [station for station in station_names if station not in df.columns]
    if missing:
        raise ValueError(f"Kolom stasiun tidak tersedia: {missing}.")
    if TARGET_STATION not in station_names:
        raise ValueError(f"Daftar stasiun harus memuat target {TARGET_STATION!r}.")

    input_stations = [station for station in station_names if station != TARGET_STATION]
    return build_forecast_features(
        df,
        upstream_stations=input_stations,
        target=TARGET_STATION,
        extra_columns=extra_columns,
    )


def build_multistation_targets(
    df: pd.DataFrame,
    horizons: list[int],
    horizon_steps: dict[int, int],
    stations: list[str] | None = None,
) -> dict[int, pd.DataFrame]:
    """Bangun target masa depan (baris waktu, kolom stasiun) per horizon."""
    station_names = list(stations or ALL_STATIONS)
    missing = [station for station in station_names if station not in df.columns]
    if missing:
        raise ValueError(f"Kolom target stasiun tidak tersedia: {missing}.")
    missing_horizons = [h for h in horizons if h not in horizon_steps]
    if missing_horizons:
        raise ValueError(f"horizon_steps tidak memuat horizon: {missing_horizons}.")

    return {
        h: df[station_names].shift(-horizon_steps[h])
        for h in horizons
    }


def align_multistation_features_targets(
    X: pd.DataFrame,
    y_horizons: dict[int, pd.DataFrame],
) -> tuple[pd.DataFrame, dict[int, pd.DataFrame]]:
    """Selaraskan fitur dengan seluruh target multi-stasiun tanpa nilai hilang."""
    valid_idx = X.dropna().index
    for targets in y_horizons.values():
        valid_idx = valid_idx.intersection(targets.dropna().index)

    return X.loc[valid_idx], {
        horizon: targets.loc[valid_idx]
        for horizon, targets in y_horizons.items()
    }


def build_multistation_dataset_from_segments(
    segments: list[DataSegment],
    horizons: list[int],
    horizon_steps: dict[int, int],
    stations: list[str] | None = None,
    extra_columns: list[str] | None = None,
) -> tuple[pd.DataFrame, dict[int, pd.DataFrame], pd.Series]:
    """Bangun dataset multi-stasiun per segmen agar fitur/target tidak melintasi gap."""
    feature_parts: list[pd.DataFrame] = []
    target_parts: dict[int, list[pd.DataFrame]] = {h: [] for h in horizons}
    source_parts: list[pd.Series] = []

    for segment in segments:
        frame = segment.df.copy()
        if extra_columns:
            for column in extra_columns:
                if segment.rainfall is not None and column == segment.rainfall.name:
                    frame[column] = segment.rainfall
                elif column not in frame.columns:
                    frame[column] = 0.0

        X_segment = build_multistation_features(
            frame,
            stations=stations,
            extra_columns=extra_columns,
        )
        y_segment = build_multistation_targets(
            frame,
            horizons=horizons,
            horizon_steps=horizon_steps,
            stations=stations,
        )
        X_segment, y_segment = align_multistation_features_targets(
            X_segment, y_segment,
        )
        feature_parts.append(X_segment)
        for horizon in horizons:
            target_parts[horizon].append(y_segment[horizon])
        source_parts.append(pd.Series(segment.label, index=X_segment.index, name="source"))

    return (
        pd.concat(feature_parts, axis=0),
        {
            horizon: pd.concat(parts, axis=0)
            for horizon, parts in target_parts.items()
        },
        pd.concat(source_parts, axis=0),
    )
