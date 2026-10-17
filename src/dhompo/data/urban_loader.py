"""Preprocess the urban 30-minute wide water-level dataset.

This module is separate from ``dhompo.data.loader`` because the Dhompo models
use a fixed river-station schema that is not compatible with the urban CSV.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from dhompo.config import load_yaml_config, resolve_path_from_config

DEFAULT_CONFIG_PATH = "configs/surabaya/urban_water_level.yaml"
QUALITY_OK = "OK"
QUALITY_MISSING = "MISSING"
QUALITY_STALE = "STALE"
QUALITY_OUTLIER = "OUTLIER"


@dataclass(frozen=True)
class UrbanSignalSpec:
    """Mapping from one canonical feature to one or more raw component columns."""

    canonical: str
    components: tuple[str, ...]
    kind: str
    location_key: str
    location_name: str
    transform: dict[str, Any] | None = None


@dataclass(frozen=True)
class UrbanPreprocessedData:
    """Preprocessed dataset ready for feature engineering/training prototypes."""

    raw: pd.DataFrame
    canonical: pd.DataFrame
    modeling_canonical: pd.DataFrame
    values: pd.DataFrame
    quality_flags: pd.DataFrame
    coverage: pd.DataFrame
    target_column: str
    feature_columns: list[str]
    dropped_empty_raw_columns: list[str]
    max_ffill_steps: int
    outlier_summary: pd.DataFrame


def load_urban_config(path: str | Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    """Load the urban water-level preprocessing config."""

    return load_yaml_config(path)


def signal_specs(config: dict[str, Any]) -> list[UrbanSignalSpec]:
    """Return canonical signal mappings from the config."""

    specs: list[UrbanSignalSpec] = []
    for loc in config.get("locations", []):
        signals: list[tuple[str, dict[str, Any]]] = []
        for kind in ("water_level", "rainfall"):
            signal = loc.get(kind)
            if signal:
                signals.append((kind, signal))
        telemetry = loc.get("telemetry") or []
        if isinstance(telemetry, dict):
            telemetry = [telemetry]
        signals.extend(("telemetry", signal) for signal in telemetry)
        for kind, signal in signals:
            specs.append(
                UrbanSignalSpec(
                    canonical=str(signal["canonical"]),
                    components=tuple(str(c) for c in signal.get("components", [])),
                    kind=kind,
                    location_key=str(loc["key"]),
                    location_name=str(loc["name"]),
                    transform=signal.get("transform"),
                )
            )
    return specs


def resolve_urban_data_path(
    config: dict[str, Any],
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    data_path: str | Path | None = None,
) -> Path:
    """Resolve the urban CSV path, honoring explicit overrides first."""

    if data_path is not None:
        return Path(data_path)
    resolved = resolve_path_from_config(config_path, config.get("data_path"))
    if resolved is None:
        raise ValueError("Urban config must define data_path when no override is given.")
    return resolved


def load_urban_wide_csv(
    path: str | Path,
    timestamp_column: str = "timestamp",
    cadence_minutes: int = 30,
) -> pd.DataFrame:
    """Load a wide CSV indexed at the expected real-time cadence."""

    df = pd.read_csv(path)
    if timestamp_column not in df.columns:
        raise ValueError(f"Missing timestamp column: {timestamp_column}")

    df[timestamp_column] = pd.to_datetime(df[timestamp_column])
    df = df.set_index(timestamp_column).sort_index()
    df.index.name = "Datetime"
    df = df.apply(pd.to_numeric, errors="coerce")
    return df.asfreq(f"{cadence_minutes}min")


def canonicalize_urban_wide(
    raw: pd.DataFrame,
    specs: list[UrbanSignalSpec],
) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """Merge A/B component columns into canonical per-location signals.

    If both A and B are present at a timestamp, their mean is used. If only one
    side is present, that value is used. Missing raw component columns are
    treated as fully empty so the config can stay stable while sensors evolve.
    """

    canonical_cols: dict[str, pd.Series] = {}
    coverage_rows: list[dict[str, Any]] = []
    configured_raw = {c for spec in specs for c in spec.components}
    dropped_empty_raw_columns: list[str] = []

    for spec in specs:
        component_values = []
        component_non_null = 0
        for component in spec.components:
            if component in raw.columns:
                series = raw[component]
            else:
                series = pd.Series(float("nan"), index=raw.index, dtype="float64")
            numeric = pd.to_numeric(series, errors="coerce")
            component_non_null += int(numeric.notna().sum())
            component_values.append(numeric)
            if int(numeric.notna().sum()) == 0:
                dropped_empty_raw_columns.append(component)

        if component_values:
            merged = pd.concat(component_values, axis=1).mean(axis=1, skipna=True)
        else:
            merged = pd.Series(float("nan"), index=raw.index, dtype="float64")

        transform = spec.transform or {}
        transform_type = transform.get("type")
        if transform_type == "reference_minus_distance":
            merged = float(transform["reference_cm"]) - merged
        elif transform_type not in (None, "identity"):
            raise ValueError(f"Unsupported signal transform: {transform_type}")

        canonical_cols[spec.canonical] = merged.astype("float64")
        non_null = int(merged.notna().sum())
        coverage_rows.append(
            {
                "column": spec.canonical,
                "kind": spec.kind,
                "location_key": spec.location_key,
                "location_name": spec.location_name,
                "non_null": non_null,
                "total": len(raw),
                "coverage": non_null / len(raw) if len(raw) else 0.0,
                "raw_component_non_null": component_non_null,
            }
        )

    unknown_columns = sorted(set(raw.columns) - configured_raw)
    for col in unknown_columns:
        if int(raw[col].notna().sum()) == 0:
            dropped_empty_raw_columns.append(col)

    canonical = pd.DataFrame(canonical_cols, index=raw.index)
    coverage = pd.DataFrame(coverage_rows)
    return canonical, coverage, sorted(set(dropped_empty_raw_columns))


def clean_target_for_modeling(
    canonical: pd.DataFrame,
    target_column: str,
    cleaning_config: dict[str, Any] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Flag implausible target values and remove them from modeling labels/features."""

    cleaned = canonical.copy()
    flags = pd.DataFrame(QUALITY_OK, index=canonical.index, columns=canonical.columns)
    if not cleaning_config or not bool(cleaning_config.get("enabled", False)):
        return cleaned, flags

    target = cleaned[target_column]
    outlier = pd.Series(False, index=cleaned.index)
    min_cm = cleaning_config.get("min_cm")
    max_cm = cleaning_config.get("max_cm")
    max_step = cleaning_config.get("max_step_change_cm")

    if bool(cleaning_config.get("zero_as_outlier", False)):
        outlier |= target == 0.0
    if min_cm is not None:
        outlier |= target < float(min_cm)
    if max_cm is not None:
        outlier |= target > float(max_cm)
    if max_step is not None:
        outlier |= target.diff().abs() > float(max_step)

    outlier = outlier.fillna(False) & target.notna()
    cleaned.loc[outlier, target_column] = float("nan")
    flags.loc[outlier, target_column] = QUALITY_OUTLIER
    return cleaned, flags


def summarize_outliers(
    original: pd.DataFrame,
    cleaned: pd.DataFrame,
    target_column: str,
) -> pd.DataFrame:
    target = original[target_column]
    removed = target.notna() & cleaned[target_column].isna()
    return pd.DataFrame(
        [
            {
                "Kolom": target_column,
                "Observed_Before_Cleaning": int(target.notna().sum()),
                "Observed_After_Cleaning": int(cleaned[target_column].notna().sum()),
                "Removed_As_Outlier": int(removed.sum()),
                "Removed_Percentage": (
                    round(float(removed.sum() / target.notna().sum() * 100), 4)
                    if int(target.notna().sum()) > 0
                    else 0.0
                ),
            }
        ]
    )


def select_feature_columns_by_coverage(
    coverage: pd.DataFrame,
    target_column: str,
    min_coverage: float,
) -> list[str]:
    selected = [
        str(row.column)
        for row in coverage.itertuples(index=False)
        if float(row.coverage) >= min_coverage
    ]
    if target_column not in selected:
        selected.insert(0, target_column)
    return selected


def flag_and_fill_realtime_values(
    canonical: pd.DataFrame,
    max_ffill_steps: int,
    base_flags: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Forward-fill with quality flags suitable for real-time prediction.

    Raw observations are ``OK``. Values filled from recent history are
    ``STALE``. Values still absent after the fill limit are ``MISSING``.
    """

    values = canonical.ffill(limit=max_ffill_steps)
    flags = pd.DataFrame(QUALITY_OK, index=canonical.index, columns=canonical.columns)
    flags = flags.mask(canonical.isna() & values.notna(), QUALITY_STALE)
    flags = flags.mask(values.isna(), QUALITY_MISSING)
    if base_flags is not None:
        flags = flags.mask(base_flags != QUALITY_OK, base_flags)
    return values, flags


def preprocess_urban_wide_data(
    data_path: str | Path | None = None,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
) -> UrbanPreprocessedData:
    """Load, canonicalize, flag, and fill the urban wide dataset."""

    config = load_urban_config(config_path)
    csv_path = resolve_urban_data_path(config, config_path, data_path)
    cadence_minutes = int(config.get("cadence_minutes", 30))
    timestamp_column = str(config.get("timestamp_column", "timestamp"))
    max_ffill_steps = int(config.get("max_ffill_steps", 12))
    target_column = str(config.get("target_column"))
    min_coverage = float(config.get("features", {}).get("min_coverage", 0.0))

    raw = load_urban_wide_csv(
        csv_path,
        timestamp_column=timestamp_column,
        cadence_minutes=cadence_minutes,
    )
    canonical, coverage, dropped = canonicalize_urban_wide(raw, signal_specs(config))

    if target_column not in canonical.columns:
        raise ValueError(f"Configured target_column is not canonicalized: {target_column}")
    if int(canonical[target_column].notna().sum()) == 0:
        raise ValueError(f"Configured target_column has no observed values: {target_column}")

    modeling_canonical, base_flags = clean_target_for_modeling(
        canonical,
        target_column,
        config.get("cleaning", {}).get("target", {}),
    )
    values, flags = flag_and_fill_realtime_values(
        modeling_canonical,
        max_ffill_steps,
        base_flags=base_flags,
    )
    feature_columns = select_feature_columns_by_coverage(
        coverage,
        target_column,
        min_coverage,
    )
    outlier_summary = summarize_outliers(canonical, modeling_canonical, target_column)

    return UrbanPreprocessedData(
        raw=raw,
        canonical=canonical,
        modeling_canonical=modeling_canonical,
        values=values,
        quality_flags=flags,
        coverage=coverage,
        target_column=target_column,
        feature_columns=feature_columns,
        dropped_empty_raw_columns=dropped,
        max_ffill_steps=max_ffill_steps,
        outlier_summary=outlier_summary,
    )
