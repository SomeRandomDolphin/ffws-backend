"""Builder fitur per-stasiun yang dipakai bersama oleh training dan serving Tier-A."""

from __future__ import annotations

import numpy as np
import pandas as pd

from dhompo.data.loader import ALL_STATIONS, TARGET_STATION

FEATURES_PER_STATION: int = 7
AR_LAG_DIM: int = 6
HORIZON_STEPS_PER_HOUR: int = 2

_ROLLING_WINDOW: int = 6


def build_per_station_features(
    df: pd.DataFrame, stations: list[str] | tuple[str, ...] = tuple(ALL_STATIONS),
) -> np.ndarray:
    """Reshape riwayat sensor menjadi (n_timesteps, n_stations, features_per_station)."""
    # Stasiun yang hilang diisi nol; pemanggil wajib mask-out lewat mask tensor.
    n_steps = len(df)
    n_stations = len(stations)
    out = np.zeros((n_steps, n_stations, FEATURES_PER_STATION), dtype=np.float32)
    for s_idx, station in enumerate(stations):
        if station not in df.columns:
            continue
        col = df[station]
        out[:, s_idx, 0] = col.to_numpy()
        out[:, s_idx, 1] = col.shift(1).to_numpy()
        out[:, s_idx, 2] = col.shift(2).to_numpy()
        out[:, s_idx, 3] = col.shift(3).to_numpy()
        out[:, s_idx, 4] = col.rolling(window=_ROLLING_WINDOW,
                                       min_periods=_ROLLING_WINDOW).mean().to_numpy()
        out[:, s_idx, 5] = col.rolling(window=_ROLLING_WINDOW,
                                       min_periods=_ROLLING_WINDOW).std(ddof=0).to_numpy()
        out[:, s_idx, 6] = col.diff().to_numpy()
    return out


def build_ar_lags(df: pd.DataFrame, target: str = TARGET_STATION) -> np.ndarray:
    """Susun shift(0..AR_LAG_DIM-1) dari kolom target menjadi (n_timesteps, AR_LAG_DIM)."""
    series = df[target]
    lags = np.stack([series.shift(i).to_numpy() for i in range(AR_LAG_DIM)], axis=-1)
    return lags.astype(np.float32)


def build_targets(df: pd.DataFrame, target: str = TARGET_STATION) -> np.ndarray:
    """Susun target h+1..h+5 jam menjadi (n_timesteps, 5)."""
    series = df[target]
    horizons = [
        series.shift(-h * HORIZON_STEPS_PER_HOUR).to_numpy()
        for h in range(1, 6)
    ]
    return np.stack(horizons, axis=-1).astype(np.float32)
