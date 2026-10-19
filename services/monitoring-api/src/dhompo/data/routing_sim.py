"""Kalibrasi dan simulasi transfer routing level pada graf DAS Welang."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from dhompo.data.loader import DataSegment
from dhompo.data.network import RiverNetwork


@dataclass(frozen=True)
class StationRoutingParameters:
    """Parameter dinamika lokal satu stasiun."""

    baseline_m: float
    recession: float
    rainfall_gain: float
    noise_std_m: float
    observed_std_m: float


@dataclass(frozen=True)
class ReachRoutingParameters:
    """Parameter transfer satu reach."""

    upstream: str
    downstream: str
    lag_steps: int
    attenuation: float


@dataclass(frozen=True)
class RoutingParameters:
    """Parameter lengkap simulator routing jaringan."""

    timestep_hours: float
    rain_memory: float
    spatial_rain_sigma: float
    initial_anomaly_fraction: float
    stations: dict[str, StationRoutingParameters]
    reaches: tuple[ReachRoutingParameters, ...]

    def to_dict(self) -> dict:
        return {
            "timestep_hours": self.timestep_hours,
            "rain_memory": self.rain_memory,
            "spatial_rain_sigma": self.spatial_rain_sigma,
            "initial_anomaly_fraction": self.initial_anomaly_fraction,
            "stations": {
                name: asdict(parameters)
                for name, parameters in self.stations.items()
            },
            "reaches": [asdict(parameters) for parameters in self.reaches],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RoutingParameters":
        return cls(
            timestep_hours=float(data["timestep_hours"]),
            rain_memory=float(data["rain_memory"]),
            spatial_rain_sigma=float(data["spatial_rain_sigma"]),
            initial_anomaly_fraction=float(data["initial_anomaly_fraction"]),
            stations={
                name: StationRoutingParameters(**{
                    key: float(value) for key, value in parameters.items()
                })
                for name, parameters in data["stations"].items()
            },
            reaches=tuple(
                ReachRoutingParameters(
                    upstream=str(parameters["upstream"]),
                    downstream=str(parameters["downstream"]),
                    lag_steps=int(parameters["lag_steps"]),
                    attenuation=float(parameters["attenuation"]),
                )
                for parameters in data["reaches"]
            ),
        )


def _lag_regression_coefficient(series: pd.Series, baseline: float) -> tuple[float, float]:
    anomaly = pd.to_numeric(series, errors="coerce") - baseline
    paired = pd.concat([anomaly.shift(1).rename("previous"), anomaly.rename("current")], axis=1).dropna()
    if len(paired) < 3 or float(paired["previous"].var()) == 0.0:
        return 0.8, float(anomaly.std())
    previous = paired["previous"].to_numpy(dtype=float)
    current = paired["current"].to_numpy(dtype=float)
    coefficient = float(np.dot(previous, current) / np.dot(previous, previous))
    residual = current - coefficient * previous
    return coefficient, float(np.std(residual))


def _estimate_attenuation(
    segments: list[DataSegment],
    upstream: str,
    downstream: str,
    lag_steps: int,
    baselines: dict[str, float],
    default: float,
) -> float:
    upstream_parts: list[np.ndarray] = []
    downstream_parts: list[np.ndarray] = []
    for segment in segments:
        frame = segment.df
        source = frame[upstream].shift(lag_steps) - baselines[upstream]
        target = frame[downstream] - baselines[downstream]
        paired = pd.concat([source.rename("source"), target.rename("target")], axis=1).dropna()
        if len(paired):
            upstream_parts.append(paired["source"].to_numpy(dtype=float))
            downstream_parts.append(paired["target"].to_numpy(dtype=float))
    if not upstream_parts:
        return default
    source_values = np.concatenate(upstream_parts)
    target_values = np.concatenate(downstream_parts)
    denominator = float(np.dot(source_values, source_values))
    if denominator == 0.0:
        return default
    return abs(float(np.dot(source_values, target_values) / denominator))


def fit_routing_parameters(
    segments: list[DataSegment],
    network: RiverNetwork,
    config: dict,
) -> RoutingParameters:
    """Estimasi parameter stabil dari dua segmen data acuan."""
    routing_cfg = config.get("routing", config)
    timestep_hours = float(routing_cfg.get("timestep_hours", 0.5))
    if timestep_hours <= 0:
        raise ValueError("timestep_hours harus lebih besar dari nol.")

    min_recession = float(routing_cfg.get("min_recession", 0.2))
    max_recession = float(routing_cfg.get("max_recession", 0.98))
    min_attenuation = float(routing_cfg.get("min_attenuation", 0.05))
    max_attenuation = float(routing_cfg.get("max_attenuation", 0.95))
    default_attenuation = float(routing_cfg.get("default_attenuation", 0.35))
    response_fraction = float(routing_cfg.get("rainfall_response_fraction", 0.35))
    noise_fraction = float(routing_cfg.get("process_noise_fraction", 0.05))

    station_names = network.station_names
    frames = [segment.df[station_names] for segment in segments]
    combined = pd.concat(frames, axis=0)
    baselines = {
        station: float(combined[station].median()) for station in station_names
    }
    rainfall_parts = [
        segment.rainfall for segment in segments if segment.rainfall is not None
    ]
    positive_rain = (
        pd.concat(rainfall_parts, axis=0)
        if rainfall_parts
        else pd.Series(dtype=float)
    )
    positive_rain = positive_rain[positive_rain > 0]
    rain_scale = float(positive_rain.quantile(0.95)) if len(positive_rain) else 1.0
    rain_scale = max(rain_scale, 1.0)

    station_parameters: dict[str, StationRoutingParameters] = {}
    for station in station_names:
        coefficients: list[float] = []
        residual_scales: list[float] = []
        for segment in segments:
            coefficient, residual_std = _lag_regression_coefficient(
                segment.df[station], baselines[station],
            )
            coefficients.append(coefficient)
            residual_scales.append(residual_std)
        recession = float(np.clip(np.median(coefficients), min_recession, max_recession))
        observed_std = max(float(combined[station].std()), 1e-6)
        residual_std = max(float(np.median(residual_scales)), 1e-6)
        station_parameters[station] = StationRoutingParameters(
            baseline_m=baselines[station],
            recession=recession,
            rainfall_gain=response_fraction * observed_std / rain_scale,
            noise_std_m=noise_fraction * residual_std,
            observed_std_m=observed_std,
        )

    reach_parameters: list[ReachRoutingParameters] = []
    for reach in network.reaches:
        lag_steps = max(1, int(round(reach.travel_hours / timestep_hours)))
        attenuation = _estimate_attenuation(
            segments,
            reach.upstream,
            reach.downstream,
            lag_steps,
            baselines,
            default_attenuation,
        )
        reach_parameters.append(ReachRoutingParameters(
            upstream=reach.upstream,
            downstream=reach.downstream,
            lag_steps=lag_steps,
            attenuation=float(np.clip(attenuation, min_attenuation, max_attenuation)),
        ))

    return RoutingParameters(
        timestep_hours=timestep_hours,
        rain_memory=float(routing_cfg.get("rain_memory", 0.75)),
        spatial_rain_sigma=float(routing_cfg.get("spatial_rain_sigma", 0.12)),
        initial_anomaly_fraction=float(routing_cfg.get("initial_anomaly_fraction", 0.10)),
        stations=station_parameters,
        reaches=tuple(reach_parameters),
    )


def simulate_levels(
    rainfall: pd.Series,
    parameters: RoutingParameters,
    network: RiverNetwork,
    seed: int = 42,
) -> pd.DataFrame:
    """Simulasikan level 15 stasiun dari satu skenario hujan."""
    if len(rainfall) < 2:
        raise ValueError("Minimal dua langkah hujan diperlukan untuk routing.")
    missing = set(network.station_names) - set(parameters.stations)
    if missing:
        raise ValueError(f"Parameter routing tidak memuat stasiun: {sorted(missing)}.")

    index = rainfall.index
    rain = pd.to_numeric(rainfall, errors="coerce").fillna(0.0).clip(lower=0.0).to_numpy()
    station_order = network.order_upstream_to_downstream()
    station_index = {station: idx for idx, station in enumerate(station_order)}
    anomalies = np.zeros((len(index), len(station_order)), dtype=float)
    rng = np.random.default_rng(seed)
    spatial_scale = {
        station: float(rng.lognormal(mean=0.0, sigma=parameters.spatial_rain_sigma))
        for station in station_order
    }
    for station, idx in station_index.items():
        station_parameters = parameters.stations[station]
        anomalies[0, idx] = rng.normal(
            0.0,
            station_parameters.observed_std_m * parameters.initial_anomaly_fraction,
        )

    reaches_to: dict[str, list[ReachRoutingParameters]] = {
        station: [] for station in station_order
    }
    for reach in parameters.reaches:
        reaches_to[reach.downstream].append(reach)

    rain_state = np.zeros(len(index), dtype=float)
    rain_state[0] = rain[0]
    for step in range(1, len(index)):
        rain_state[step] = parameters.rain_memory * rain_state[step - 1] + rain[step]
        for station in station_order:
            idx = station_index[station]
            station_parameters = parameters.stations[station]
            parent_signals: list[float] = []
            for reach in reaches_to[station]:
                parent_idx = station_index[reach.upstream]
                source_step = max(0, step - reach.lag_steps)
                parent_signals.append(
                    reach.attenuation * anomalies[source_step, parent_idx]
                )
            upstream_signal = float(np.mean(parent_signals)) if parent_signals else 0.0
            rainfall_signal = (
                station_parameters.rainfall_gain
                * spatial_scale[station]
                * rain_state[step]
            )
            innovation = rng.normal(0.0, station_parameters.noise_std_m)
            anomalies[step, idx] = (
                station_parameters.recession * anomalies[step - 1, idx]
                + (1.0 - station_parameters.recession) * upstream_signal
                + rainfall_signal
                + innovation
            )

    data = {}
    for station in network.station_names:
        station_parameters = parameters.stations[station]
        values = station_parameters.baseline_m + anomalies[:, station_index[station]]
        data[station] = np.clip(values, 0.0, None)
    return pd.DataFrame(data, index=index)


def forecast_levels(
    history_levels: pd.DataFrame,
    future_rainfall: pd.Series,
    parameters: RoutingParameters,
    network: RiverNetwork,
    historical_rainfall: pd.Series | None = None,
    include_process_noise: bool = False,
    seed: int = 42,
) -> pd.DataFrame:
    """Forecast level dari kondisi historis terakhir dan skenario hujan masa depan."""
    stations = network.station_names
    missing = [station for station in stations if station not in history_levels.columns]
    if missing:
        raise ValueError(f"Riwayat tidak memuat stasiun: {missing}.")
    if len(history_levels) < 2:
        raise ValueError("Minimal dua baris riwayat level diperlukan.")
    if len(future_rainfall) < 1:
        raise ValueError("Minimal satu langkah hujan masa depan diperlukan.")

    station_order = network.order_upstream_to_downstream()
    station_index = {station: idx for idx, station in enumerate(station_order)}
    history = history_levels[station_order].astype(float)
    n_history = len(history)
    n_future = len(future_rainfall)
    anomalies = np.zeros((n_history + n_future, len(station_order)), dtype=float)
    for station, idx in station_index.items():
        anomalies[:n_history, idx] = (
            history[station].to_numpy()
            - parameters.stations[station].baseline_m
        )

    history_rain = (
        pd.to_numeric(historical_rainfall, errors="coerce")
        .reindex(history.index)
        .fillna(0.0)
        .clip(lower=0.0)
        .to_numpy()
        if historical_rainfall is not None
        else np.zeros(n_history, dtype=float)
    )
    future_rain = (
        pd.to_numeric(future_rainfall, errors="coerce")
        .fillna(0.0)
        .clip(lower=0.0)
        .to_numpy()
    )
    rain_state = np.zeros(n_history + n_future, dtype=float)
    for step in range(n_history):
        previous = rain_state[step - 1] if step else 0.0
        rain_state[step] = parameters.rain_memory * previous + history_rain[step]

    reaches_to: dict[str, list[ReachRoutingParameters]] = {
        station: [] for station in station_order
    }
    for reach in parameters.reaches:
        reaches_to[reach.downstream].append(reach)

    rng = np.random.default_rng(seed)
    for future_step in range(n_future):
        step = n_history + future_step
        rain_state[step] = (
            parameters.rain_memory * rain_state[step - 1]
            + future_rain[future_step]
        )
        for station in station_order:
            idx = station_index[station]
            station_parameters = parameters.stations[station]
            parent_signals = []
            for reach in reaches_to[station]:
                source_step = max(0, step - reach.lag_steps)
                parent_signals.append(
                    reach.attenuation
                    * anomalies[source_step, station_index[reach.upstream]]
                )
            upstream_signal = float(np.mean(parent_signals)) if parent_signals else 0.0
            rainfall_signal = station_parameters.rainfall_gain * rain_state[step]
            innovation = (
                rng.normal(0.0, station_parameters.noise_std_m)
                if include_process_noise
                else 0.0
            )
            anomalies[step, idx] = (
                station_parameters.recession * anomalies[step - 1, idx]
                + (1.0 - station_parameters.recession) * upstream_signal
                + rainfall_signal
                + innovation
            )

    future_anomalies = anomalies[n_history:]
    output = {}
    for station in stations:
        values = (
            parameters.stations[station].baseline_m
            + future_anomalies[:, station_index[station]]
        )
        output[station] = np.clip(values, 0.0, None)
    return pd.DataFrame(output, index=future_rainfall.index)


__all__ = [
    "ReachRoutingParameters",
    "RoutingParameters",
    "StationRoutingParameters",
    "fit_routing_parameters",
    "forecast_levels",
    "simulate_levels",
]
