"""Load and preprocess Dhompo sensor data."""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import pandas as pd

from dhompo.config import PROJECT_ROOT

# Canonical station ordering (elevation descending)
STATION_META: dict[str, tuple[float, int]] = {
    "Bd. Sentono":     (680.536, 1),
    "Bd. Suwoto":      (503,     2),
    "Krajan Timur":    (335,     3),
    "Purwodadi":       (287,     4),
    "Bd. Baong":       (169,     5),
    "Bd. Lecari":      (167,     6),
    "Bd. Bakalan":     (136,     7),
    "AWLR Kademungan": (128,     8),
    "Bd. Domas":       (57,      9),
    "Bd Guyangan":     (32,     10),
    "Bd. Grinting":    (28,     11),
    "Sidogiri":        (24,     12),
    "Klosod":          (22,     13),
    "Dhompo":          (7,      14),
    "Jalan Nasional":  (1.8,    15),
}

UPSTREAM_STATIONS: list[str] = [
    "Bd. Suwoto",
    "Krajan Timur",
    "Purwodadi",
    "Bd. Lecari",
    "Bd. Bakalan",
    "Bd. Baong",
    "AWLR Kademungan",
    "Bd Guyangan",
    "Sidogiri",
    "Bd. Domas",
    "Klosod",
    "Bd. Grinting",
]

ALL_STATIONS: list[str] = list(STATION_META.keys())
TARGET_STATION: str = "Dhompo"

_DEFAULT_DATA_PATH = PROJECT_ROOT / "data" / "data-clean.csv"

GOLD_DATA_PATH = PROJECT_ROOT / "data" / "gold" / "hydro.csv"
GOLD_STATIONS_PATH = PROJECT_ROOT / "data" / "gold" / "stations.csv"

# Mapping from generated-data column names → canonical names
GENERATED_COLUMN_MAP: dict[str, str] = {
    "Suwoto": "Bd. Suwoto",
    "Kademungan": "AWLR Kademungan",
    "bd. Domas": "Bd. Domas",
    "Jl. Pantura": "Jalan Nasional",
    "Guyangan": "Bd Guyangan",
    "Bd Lecari": "Bd. Lecari",
}

RAINFALL_COLUMN: str = "Curah hujan"


class GeneratedData(NamedTuple):
    """Result of loading the generated-2023 Excel file."""
    stations: pd.DataFrame  # station columns with canonical names, DatetimeIndex
    rainfall: pd.Series     # rainfall series aligned to same index


def load_data(path: str | Path | None = None) -> pd.DataFrame:
    """Load cleaned sensor data, indexed by Datetime at 30-minute frequency.

    Parameters
    ----------
    path:
        CSV file path. Defaults to ``data/data-clean.csv`` relative to project root.

    Returns
    -------
    pd.DataFrame
        DataFrame with DatetimeIndex at 30-min frequency.
    """
    csv_path = Path(path) if path else _DEFAULT_DATA_PATH
    df = pd.read_csv(csv_path, parse_dates=["Datetime"], index_col="Datetime")
    df = df.asfreq("30min")
    return df


def load_gold_data(path: str | Path | None = None) -> pd.DataFrame:
    """Load the merged gold dataset produced by ``scripts/build_gold.py``.

    The frame has a DatetimeIndex named ``timestamp``, a ``source``
    column (``2022_clean`` / ``2023_generated``), a ``rain_mm`` column
    (NaN for 2022), and canonical station columns. Segments are NOT
    contiguous: there is a ~26-day gap between the two sources.
    """
    csv_path = Path(path) if path else GOLD_DATA_PATH
    df = pd.read_csv(csv_path, parse_dates=["timestamp"], index_col="timestamp")
    return df.sort_index()


def load_gold_stations(path: str | Path | None = None) -> pd.DataFrame:
    """Load gold station metadata (coords, elevation, branch, travel)."""
    csv_path = Path(path) if path else GOLD_STATIONS_PATH
    return pd.read_csv(csv_path).set_index("station")


def split_gold_segments(gold: pd.DataFrame) -> list[pd.DataFrame]:
    """Split the gold frame into contiguous 30-min segments."""
    segs: list[pd.DataFrame] = []
    for _, seg in gold.groupby("source", sort=True):
        seg = seg.sort_index()
        gap = seg.index.to_series().diff()
        starts = [0, *(int(i) for i in gap[gap > pd.Timedelta("30min")].index)]
        for s, e in zip(starts, [*starts[1:], len(seg)]):
            chunk = seg.iloc[s:e]
            if len(chunk):
                segs.append(chunk)
    return segs


def load_generated_data(path: str | Path) -> GeneratedData:
    """Load the 'Data generated 2023' Excel file.

    The file has header at row 1 (0-indexed), an unnamed index column,
    a 'Curah hujan' (rainfall) column, a 'Time' column, and station columns
    with slightly different names than the canonical set.

    Parameters
    ----------
    path:
        Path to the Excel file.

    Returns
    -------
    GeneratedData
        Named tuple with `stations` (DataFrame) and `rainfall` (Series).
    """
    df = pd.read_excel(path, header=1)

    # Drop unnamed index column
    df = df.drop(columns=[c for c in df.columns if "Unnamed" in str(c)])

    # Extract rainfall before renaming
    rainfall = df[RAINFALL_COLUMN].copy()

    # Parse datetime and set as index
    df["Time"] = pd.to_datetime(df["Time"])
    df = df.set_index("Time")
    df.index.name = "Datetime"
    df = df.asfreq("30min")

    rainfall.index = df.index
    # NaN rainfall = no rain recorded → fill with 0
    rainfall = rainfall.fillna(0.0)

    # Rename station columns to canonical names
    df = df.rename(columns=GENERATED_COLUMN_MAP)

    # Separate station data from rainfall
    station_cols = [c for c in df.columns if c != RAINFALL_COLUMN]
    stations = df[station_cols]

    rainfall = rainfall.asfreq("30min")
    rainfall.name = RAINFALL_COLUMN

    return GeneratedData(stations=stations, rainfall=rainfall)


class DataSegment(NamedTuple):
    """A contiguous time segment of sensor data."""
    df: pd.DataFrame
    label: str
    rainfall: pd.Series | None


def load_combined_data(
    clean_path: str | Path,
    generated_path: str | Path,
) -> list[DataSegment]:
    """Load both datasets as separate segments (not concatenated).

    The two datasets have a ~26-day gap (Dec 5 2022 → Jan 1 2023), so they
    are returned as a list of segments to prevent lag/rolling features from
    being contaminated by the gap.

    Parameters
    ----------
    clean_path:
        Path to data-clean.csv.
    generated_path:
        Path to Data generated 2023.xlsx.

    Returns
    -------
    list[DataSegment]
        Two segments: [clean_2022, generated_2023].
    """
    df_clean = load_data(clean_path)
    gen = load_generated_data(generated_path)

    # Use only the canonical station columns present in both datasets
    common_cols = [c for c in ALL_STATIONS if c in df_clean.columns and c in gen.stations.columns]
    df_clean = df_clean[common_cols]
    gen_stations = gen.stations[common_cols]

    return [
        DataSegment(df=df_clean, label="2022_clean", rainfall=None),
        DataSegment(df=gen_stations, label="2023_generated", rainfall=gen.rainfall),
    ]
