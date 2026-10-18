"""Detektor quality-flag untuk deret waktu sensor."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import pandas as pd

from dhompo.data.clusters import (
    OFFLINE_STALE_SECONDS,
    TELEMETRY_STALE_SECONDS,
    is_telemetry,
)


class QualityFlag(str, Enum):
    OK = "OK"
    STUCK = "STUCK"
    FLATLINE = "FLATLINE"
    OUT_OF_RANGE = "OUT_OF_RANGE"
    MISSING = "MISSING"
    STALE = "STALE"


BAD_FLAGS: frozenset[QualityFlag] = frozenset(
    {QualityFlag.STUCK, QualityFlag.FLATLINE, QualityFlag.OUT_OF_RANGE,
     QualityFlag.MISSING}
)


@dataclass(frozen=True)
class QualityConfig:
    """Ambang batas detektor."""

    flatline_window: int = 6
    flatline_var_threshold: float = 1e-4
    stuck_self_delta: float = 0.005
    stuck_neighbour_delta: float = 0.02
    stuck_window: int = 6
    out_of_range_max_step: float = 1.0
    out_of_range_pad: float = 0.5
    flood_regime_threshold: float = 9.0
    hysteresis_clear_count: int = 3
    min_plausible: dict[str, float] = field(default_factory=dict)
    training_max: dict[str, float] = field(default_factory=dict)


DEFAULT_CONFIG = QualityConfig()


def _rolling_var(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window, min_periods=window).var(ddof=0)


def _rolling_abs_delta(series: pd.Series, window: int) -> pd.Series:
    return series.diff().abs().rolling(window=window, min_periods=window).max()


def _detect_missing(values: pd.DataFrame) -> pd.DataFrame:
    return values.isna()


def _detect_out_of_range(
    values: pd.DataFrame, config: QualityConfig
) -> pd.DataFrame:
    out = pd.DataFrame(False, index=values.index, columns=values.columns)
    for station in values.columns:
        col = values[station]
        bad = col < 0.0
        max_train = config.training_max.get(station)
        if max_train is not None:
            bad |= col > (max_train + config.out_of_range_pad)
        step = col.diff().abs()
        bad |= step > config.out_of_range_max_step
        out[station] = bad.fillna(False)
    return out


def _detect_flatline(
    values: pd.DataFrame, config: QualityConfig
) -> pd.DataFrame:
    flag = pd.DataFrame(False, index=values.index, columns=values.columns)
    flood_mask = (values >= config.flood_regime_threshold).fillna(False)
    for station in values.columns:
        var = _rolling_var(values[station], config.flatline_window)
        is_flat = (var < config.flatline_var_threshold) & ~flood_mask[station]
        flag[station] = is_flat.fillna(False)
        baseline = config.min_plausible.get(station, None)
        if baseline is None or baseline > 0.0:
            zero_shortcut = (values[station] == 0.0) & ~flood_mask[station]
            flag[station] |= zero_shortcut.fillna(False)
    return flag


def _detect_stuck(
    values: pd.DataFrame, config: QualityConfig
) -> pd.DataFrame:
    flag = pd.DataFrame(False, index=values.index, columns=values.columns)
    abs_delta = values.diff().abs()
    for station in values.columns:
        self_max = abs_delta[station].rolling(
            window=config.stuck_window, min_periods=config.stuck_window
        ).max()
        neighbour_cols = [c for c in values.columns if c != station]
        if not neighbour_cols:
            continue
        neighbour_med = abs_delta[neighbour_cols].median(axis=1).rolling(
            window=config.stuck_window, min_periods=config.stuck_window
        ).median()
        is_stuck = (self_max < config.stuck_self_delta) & (
            neighbour_med > config.stuck_neighbour_delta
        )
        flag[station] = is_stuck.fillna(False)
    return flag


def _detect_stale(
    values: pd.DataFrame, reference_time: pd.Timestamp | None
) -> pd.DataFrame:
    flag = pd.DataFrame(False, index=values.index, columns=values.columns)
    if reference_time is None:
        reference_time = values.index.max()
    if pd.isna(reference_time):
        return flag
    last_idx = values.index[-1]
    age_seconds = (reference_time - last_idx).total_seconds()
    last_row = pd.Series(False, index=values.columns)
    for station in values.columns:
        threshold = (
            TELEMETRY_STALE_SECONDS
            if is_telemetry(station)
            else OFFLINE_STALE_SECONDS
        )
        last_row[station] = age_seconds > threshold
    flag.loc[last_idx] = last_row
    return flag


def _apply_hysteresis(
    sticky_flag: pd.DataFrame, config: QualityConfig
) -> pd.DataFrame:
    """Setelah True, tetap True sampai ``hysteresis_clear_count`` reading False berturut-turut."""
    out = sticky_flag.copy()
    clear_required = config.hysteresis_clear_count
    for station in out.columns:
        col = out[station].to_numpy(copy=True)
        held = False
        clean_streak = 0
        for i, val in enumerate(col):
            if val:
                held = True
                clean_streak = 0
            else:
                if held:
                    clean_streak += 1
                    if clean_streak >= clear_required:
                        held = False
                        clean_streak = 0
                    else:
                        col[i] = True
        out[station] = col
    return out


def compute_quality_flags(
    values: pd.DataFrame,
    config: QualityConfig | None = None,
    reference_time: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Hitung quality flag per-stasiun di seluruh riwayat."""
    # Prioritas: MISSING > OUT_OF_RANGE > STALE > STUCK > FLATLINE > OK.
    if config is None:
        config = DEFAULT_CONFIG
    if values.empty:
        return values.copy().astype(object)

    flags = pd.DataFrame(
        QualityFlag.OK.value, index=values.index, columns=values.columns,
        dtype=object,
    )

    flatline = _apply_hysteresis(_detect_flatline(values, config), config)
    stuck = _apply_hysteresis(_detect_stuck(values, config), config)
    stale = _detect_stale(values, reference_time)
    out_of_range = _detect_out_of_range(values, config)
    missing = _detect_missing(values)

    flags = flags.mask(flatline, QualityFlag.FLATLINE.value)
    flags = flags.mask(stuck, QualityFlag.STUCK.value)
    flags = flags.mask(stale, QualityFlag.STALE.value)
    flags = flags.mask(out_of_range, QualityFlag.OUT_OF_RANGE.value)
    flags = flags.mask(missing, QualityFlag.MISSING.value)
    return flags


def latest_flags(flags: pd.DataFrame) -> dict[str, str]:
    if flags.empty:
        return {}
    last = flags.iloc[-1]
    return {station: str(flag) for station, flag in last.items()}
