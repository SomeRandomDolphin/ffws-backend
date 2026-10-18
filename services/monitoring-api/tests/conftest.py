"""Shared test fixtures."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from dhompo.data.loader import UPSTREAM_STATIONS, TARGET_STATION

# Realistic water level ranges (m) per station, based on STATION_META elevations
_LEVEL_RANGES: dict[str, tuple[float, float]] = {
    "Bd. Suwoto": (0.3, 1.5),
    "Krajan Timur": (0.2, 1.2),
    "Purwodadi": (0.2, 1.0),
    "Bd. Baong": (0.3, 1.8),
    "Bd. Lecari": (0.2, 1.3),
    "Bd. Bakalan": (0.3, 1.5),
    "AWLR Kademungan": (0.2, 1.4),
    "Bd. Domas": (0.3, 2.0),
    "Bd Guyangan": (0.2, 1.6),
    "Bd. Grinting": (0.3, 1.8),
    "Sidogiri": (0.2, 1.5),
    "Klosod": (0.3, 2.0),
    "Dhompo": (0.5, 3.0),
}

ALL_REQUIRED = UPSTREAM_STATIONS + [TARGET_STATION]


def make_history_df(n_rows: int = 48, seed: int = 42) -> pd.DataFrame:
    """Create a synthetic history DataFrame with DatetimeIndex.

    Returns a DataFrame suitable for FilePredictor.predict_from_history().
    """
    rng = np.random.default_rng(seed)
    start = datetime(2022, 11, 21, 0, 0)
    idx = pd.date_range(start, periods=n_rows, freq="30min", name="Datetime")

    data: dict[str, np.ndarray] = {}
    for st in ALL_REQUIRED:
        lo, hi = _LEVEL_RANGES.get(st, (0.2, 1.5))
        data[st] = rng.uniform(lo, hi, size=n_rows)

    return pd.DataFrame(data, index=idx)


def make_history_payload(n_rows: int = 48, seed: int = 42) -> list[dict]:
    """Create a history payload (list of dicts) for the API request body."""
    df = make_history_df(n_rows, seed)
    rows = []
    for ts, row in df.iterrows():
        rows.append({
            "timestamp": ts.isoformat(),
            "readings": row.to_dict(),
        })
    return rows


@pytest.fixture
def sample_history_df() -> pd.DataFrame:
    """48-row synthetic history DataFrame."""
    return make_history_df(48)


@pytest.fixture
def sample_history_payload() -> list[dict]:
    """48-row history payload for API requests."""
    return make_history_payload(48)
