"""Generator curah hujan stokastik Markov-Gamma untuk skenario DAS Welang."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class RainfallParameters:
    """Parameter transisi basah/kering dan intensitas Gamma."""

    p_wet_after_dry: float
    p_wet_after_wet: float
    gamma_shape: float
    gamma_scale: float
    monthly_multipliers: tuple[float, ...]
    max_rain_mm: float = 50.0

    def to_dict(self) -> dict:
        data = asdict(self)
        data["monthly_multipliers"] = list(self.monthly_multipliers)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "RainfallParameters":
        return cls(
            p_wet_after_dry=float(data["p_wet_after_dry"]),
            p_wet_after_wet=float(data["p_wet_after_wet"]),
            gamma_shape=float(data["gamma_shape"]),
            gamma_scale=float(data["gamma_scale"]),
            monthly_multipliers=tuple(float(v) for v in data["monthly_multipliers"]),
            max_rain_mm=float(data.get("max_rain_mm", 50.0)),
        )


def fit_rainfall_parameters(
    rainfall: pd.Series,
    monthly_multipliers: list[float] | tuple[float, ...],
    wet_threshold_mm: float = 0.0,
    laplace_smoothing: float = 1.0,
    min_gamma_shape: float = 0.2,
    max_rain_mm: float = 50.0,
) -> RainfallParameters:
    """Estimasi rantai Markov dan Gamma dari deret hujan 30-menit."""
    clean = pd.to_numeric(rainfall, errors="coerce").fillna(0.0).clip(lower=0.0)
    if len(clean) < 2:
        raise ValueError("Minimal dua observasi diperlukan untuk fitting hujan.")
    multipliers = tuple(float(v) for v in monthly_multipliers)
    if len(multipliers) != 12 or any(v <= 0 for v in multipliers):
        raise ValueError("monthly_multipliers harus berisi 12 nilai positif.")
    if laplace_smoothing < 0:
        raise ValueError("laplace_smoothing tidak boleh negatif.")

    wet = clean.to_numpy() > wet_threshold_mm
    previous = wet[:-1]
    current = wet[1:]

    def transition_probability(previous_state: bool) -> float:
        selected = current[previous == previous_state]
        wet_count = float(selected.sum())
        total = float(len(selected))
        return (wet_count + laplace_smoothing) / (total + 2.0 * laplace_smoothing)

    positive = clean[wet].to_numpy(dtype=float)
    if len(positive) == 0:
        raise ValueError("Tidak ada observasi hujan positif untuk fitting intensitas.")
    mean = float(np.mean(positive))
    variance = float(np.var(positive))
    shape = mean * mean / variance if variance > 0 else 1.0
    shape = max(float(min_gamma_shape), shape)
    scale = mean / shape

    return RainfallParameters(
        p_wet_after_dry=transition_probability(False),
        p_wet_after_wet=transition_probability(True),
        gamma_shape=shape,
        gamma_scale=scale,
        monthly_multipliers=multipliers,
        max_rain_mm=float(max_rain_mm),
    )


def simulate_rainfall(
    start: str | pd.Timestamp,
    periods: int,
    parameters: RainfallParameters,
    seed: int = 42,
    frequency: str = "30min",
) -> pd.Series:
    """Bangkitkan deret hujan non-negatif dengan seed deterministik."""
    if periods <= 0:
        raise ValueError("periods harus lebih besar dari nol.")
    for probability in (parameters.p_wet_after_dry, parameters.p_wet_after_wet):
        if not 0.0 <= probability <= 1.0:
            raise ValueError("Probabilitas transisi hujan harus berada pada [0, 1].")

    index = pd.date_range(start=start, periods=periods, freq=frequency, name="timestamp")
    rng = np.random.default_rng(seed)
    stationary_denominator = (
        parameters.p_wet_after_dry + 1.0 - parameters.p_wet_after_wet
    )
    stationary_wet = (
        parameters.p_wet_after_dry / stationary_denominator
        if stationary_denominator > 0
        else 0.5
    )
    wet = bool(rng.random() < stationary_wet)
    values = np.zeros(periods, dtype=float)

    for step, timestamp in enumerate(index):
        probability = (
            parameters.p_wet_after_wet if wet else parameters.p_wet_after_dry
        )
        wet = bool(rng.random() < probability)
        if wet:
            intensity = rng.gamma(parameters.gamma_shape, parameters.gamma_scale)
            intensity *= parameters.monthly_multipliers[timestamp.month - 1]
            values[step] = min(float(intensity), parameters.max_rain_mm)

    return pd.Series(values, index=index, name="rain_mm")


__all__ = ["RainfallParameters", "fit_rainfall_parameters", "simulate_rainfall"]
