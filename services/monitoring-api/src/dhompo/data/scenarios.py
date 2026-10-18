"""API skenario ensemble hujan dan level untuk simulator DAS Welang."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import yaml

from dhompo.data.network import RiverNetwork
from dhompo.data.rainfall_sim import RainfallParameters, simulate_rainfall
from dhompo.data.routing_sim import RoutingParameters, simulate_levels


def load_simulator_parameters(
    path: str | Path,
) -> tuple[RainfallParameters, RoutingParameters]:
    """Muat parameter hujan dan routing dari artefak YAML terkalibrasi."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if "rainfall" not in raw or "routing" not in raw:
        raise ValueError("Artefak simulator harus memuat rainfall dan routing.")
    return (
        RainfallParameters.from_dict(raw["rainfall"]),
        RoutingParameters.from_dict(raw["routing"]),
    )


def simulate_scenario(
    start: str | pd.Timestamp,
    periods: int,
    rainfall_parameters: RainfallParameters,
    routing_parameters: RoutingParameters,
    network: RiverNetwork,
    seed: int,
) -> pd.DataFrame:
    """Bangkitkan satu skenario hujan dan level dengan kolom provenance."""
    rainfall = simulate_rainfall(start, periods, rainfall_parameters, seed=seed)
    levels = simulate_levels(rainfall, routing_parameters, network, seed=seed + 1)
    output = levels.copy()
    output.insert(0, "rain_mm", rainfall)
    output.insert(0, "scenario_id", f"scenario_{seed}")
    output.index.name = "timestamp"
    return output


def simulate_ensemble(
    start: str | pd.Timestamp,
    periods: int,
    rainfall_parameters: RainfallParameters,
    routing_parameters: RoutingParameters,
    network: RiverNetwork,
    seeds: list[int],
) -> pd.DataFrame:
    """Gabungkan beberapa skenario independen menjadi satu tabel long-scenario."""
    if not seeds:
        raise ValueError("Minimal satu seed diperlukan untuk ensemble.")
    return pd.concat(
        [
            simulate_scenario(
                start,
                periods,
                rainfall_parameters,
                routing_parameters,
                network,
                seed,
            )
            for seed in seeds
        ],
        axis=0,
    )


__all__ = ["load_simulator_parameters", "simulate_ensemble", "simulate_scenario"]
