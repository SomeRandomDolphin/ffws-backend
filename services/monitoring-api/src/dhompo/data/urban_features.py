"""Feature engineering for the urban water-level dataset."""

from __future__ import annotations

import numpy as np
import pandas as pd

from dhompo.data.urban_loader import QUALITY_MISSING, QUALITY_OUTLIER, QUALITY_STALE

DEFAULT_LAG_STEPS: tuple[int, ...] = (1, 2, 3)
DEFAULT_ROLLING_WINDOWS: tuple[tuple[int, str], ...] = (
    (6, "3h"),
    (12, "6h"),
    (24, "12h"),
)
HORIZON_STEPS_PER_HOUR = 2


def build_urban_forecast_features(
    values: pd.DataFrame,
    feature_columns: list[str] | None = None,
    quality_flags: pd.DataFrame | None = None,
    include_quality_flags: bool = True,
    lag_steps: tuple[int, ...] | list[int] = DEFAULT_LAG_STEPS,
    rolling_windows: tuple[tuple[int, str], ...] | list[tuple[int, str]] = DEFAULT_ROLLING_WINDOWS,
    pump_control: dict | None = None,
) -> pd.DataFrame:
    """Build time-series features for direct multi-horizon forecasting.

    ``values`` is expected to contain observed values plus limited forward-fill
    for recent missing readings. When ``quality_flags`` is supplied, remaining
    missing values are converted to a numeric sentinel (0.0) and exposed through
    ``*_flag_missing`` so sklearn models can still score sparse real-time rows.
    """

    if feature_columns is None:
        feature_columns = list(values.columns)

    cols: dict[str, pd.Series] = {}
    for col in feature_columns:
        if col not in values.columns:
            continue
        raw_series = values[col]
        series = (
            raw_series.fillna(0.0)
            if include_quality_flags and quality_flags is not None
            else raw_series
        )
        cols[f"{col}_t0"] = series
        for lag in lag_steps:
            cols[f"{col}_lag{lag}"] = series.shift(int(lag))

        for window, label in rolling_windows:
            roll = series.rolling(int(window))
            cols[f"{col}_rmean_{label}"] = roll.mean()
            cols[f"{col}_rstd_{label}"] = roll.std()

        cols[f"{col}_diff1"] = series.diff(1)
        cols[f"{col}_diff2"] = series.diff(2)

        if include_quality_flags and quality_flags is not None and col in quality_flags.columns:
            stale = (quality_flags[col] == QUALITY_STALE).astype(float)
            missing = (quality_flags[col] == QUALITY_MISSING).astype(float)
            outlier = (quality_flags[col] == QUALITY_OUTLIER).astype(float)
            cols[f"{col}_flag_stale"] = stale
            cols[f"{col}_flag_missing"] = missing
            cols[f"{col}_flag_outlier"] = outlier
            for lag in lag_steps:
                cols[f"{col}_flag_stale_lag{lag}"] = stale.shift(int(lag))
                cols[f"{col}_flag_missing_lag{lag}"] = missing.shift(int(lag))
                cols[f"{col}_flag_outlier_lag{lag}"] = outlier.shift(int(lag))

    pump_control = pump_control or {}
    level_column = pump_control.get("level_column")
    if level_column in values.columns:
        level = values[level_column]
        startup = pump_control.get("startup_threshold_cm")
        shutoff = pump_control.get("shutoff_threshold_cm")
        if startup is not None:
            cols["pump_level_above_startup"] = (level >= float(startup)).astype(float)
            cols["pump_margin_to_startup_cm"] = level - float(startup)
        if shutoff is not None:
            cols["pump_level_below_shutoff"] = (level <= float(shutoff)).astype(float)
            cols["pump_margin_to_shutoff_cm"] = level - float(shutoff)
        if startup is not None and shutoff is not None:
            cols["pump_level_in_hysteresis_band"] = (
                (level > float(shutoff)) & (level < float(startup))
            ).astype(float)

    activity_columns = [
        str(column)
        for column in pump_control.get("activity_columns", [])
        if str(column) in values.columns
    ]
    if activity_columns:
        threshold = float(pump_control.get("active_threshold", 0.0))
        active_matrix = values[activity_columns].fillna(0.0).gt(threshold)
        pump_active = active_matrix.any(axis=1).astype(float)
        cols["pump_active"] = pump_active
        cols["pump_count_active"] = active_matrix.sum(axis=1).astype(float)
        run_group = pump_active.ne(pump_active.shift()).cumsum()
        cols["pump_runtime_steps"] = (
            pump_active.groupby(run_group).cumcount().add(1).mul(pump_active)
        )
        cols["pump_state_changed"] = pump_active.diff().abs().fillna(0.0)

    hour = values.index.hour + values.index.minute / 60.0
    cols["hour_sin"] = pd.Series(np.sin(2 * np.pi * hour / 24), index=values.index)
    cols["hour_cos"] = pd.Series(np.cos(2 * np.pi * hour / 24), index=values.index)
    cols["dayofweek"] = pd.Series(values.index.dayofweek.astype(float), index=values.index)
    cols["is_night"] = pd.Series(
        ((values.index.hour >= 19) | (values.index.hour < 6)).astype(float),
        index=values.index,
    )

    return pd.concat(cols, axis=1).dropna()


def build_urban_targets(
    canonical: pd.DataFrame,
    target_column: str,
    horizons: list[int],
    horizon_steps_per_hour: int = HORIZON_STEPS_PER_HOUR,
) -> dict[int, pd.Series]:
    """Build future observed target series for each horizon."""

    if target_column not in canonical.columns:
        raise ValueError(f"Target column not found: {target_column}")
    target = canonical[target_column]
    return {
        int(h): target.shift(-int(h) * horizon_steps_per_hour)
        for h in horizons
    }


def build_urban_delta_targets(
    canonical: pd.DataFrame,
    current_values: pd.DataFrame,
    target_column: str,
    horizons: list[int],
    horizon_steps_per_hour: int = HORIZON_STEPS_PER_HOUR,
) -> dict[int, pd.Series]:
    """Build future delta targets: target(t+h) - current_target(t)."""

    future = build_urban_targets(
        canonical,
        target_column=target_column,
        horizons=horizons,
        horizon_steps_per_hour=horizon_steps_per_hour,
    )
    if target_column not in current_values.columns:
        raise ValueError(f"Current target column not found: {target_column}")
    current = current_values[target_column]
    return {h: y - current for h, y in future.items()}


def align_urban_features_targets(
    X: pd.DataFrame,
    y_horizons: dict[int, pd.Series],
) -> tuple[pd.DataFrame, dict[int, pd.Series]]:
    """Keep only timestamps with complete features and observed future targets."""

    valid_idx = X.index
    for y in y_horizons.values():
        valid_idx = valid_idx.intersection(y.dropna().index)

    return X.loc[valid_idx], {h: y.loc[valid_idx] for h, y in y_horizons.items()}
