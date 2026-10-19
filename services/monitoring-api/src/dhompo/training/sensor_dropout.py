"""Sensor dropout Bernoulli per-stasiun yang regime-conditional."""

from __future__ import annotations

from dataclasses import dataclass

try:
    import torch
except ImportError as exc:
    raise ImportError(
        "Install via: pip install -r requirements-torch.txt"
    ) from exc


@dataclass(frozen=True)
class DropoutSchedule:
    """Probabilitas drop Bernoulli per regime."""

    normal: float = 0.30
    elevated: float = 0.30
    flood: float = 0.30


def regime_for_target(
    target_value: torch.Tensor,
    elevated_threshold: float = 7.0,
    flood_threshold: float = 9.0,
) -> torch.Tensor:
    """Klasifikasikan tiap elemen batch sebagai 0=normal, 1=elevated, 2=banjir."""
    regime = torch.zeros_like(target_value, dtype=torch.long)
    regime = torch.where(target_value >= elevated_threshold,
                          torch.ones_like(regime), regime)
    regime = torch.where(target_value >= flood_threshold,
                          torch.full_like(regime, 2), regime)
    return regime


def apply_sensor_dropout(
    station_features: torch.Tensor,
    mask: torch.Tensor,
    target_value: torch.Tensor,
    schedule: DropoutSchedule | None = None,
    rng: torch.Generator | None = None,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Terapkan dropout Bernoulli per-stasiun ke fitur dan mask."""
    # Dropout hanya membalik stasiun sehat menjadi rusak, tidak sebaliknya.
    if schedule is None:
        schedule = DropoutSchedule()

    regime = regime_for_target(target_value)
    drop_p = torch.tensor(
        [schedule.normal, schedule.elevated, schedule.flood],
        device=station_features.device, dtype=station_features.dtype,
    )[regime]                                            # (batch,)
    drop_p = drop_p.unsqueeze(-1).expand_as(mask)        # (batch, n_stations)

    if rng is None:
        coin = torch.rand_like(drop_p)
    else:
        coin = torch.empty_like(drop_p)
        coin.uniform_(0.0, 1.0, generator=rng)

    drop_event = coin < drop_p                           # True → drop sel ini
    new_mask = mask & ~drop_event                        # hanya 1→0, tidak pernah sebaliknya
    feature_mask = new_mask.unsqueeze(-1).to(station_features.dtype)
    new_features = station_features * feature_mask       # nolkan nilai yang di-drop

    return new_features, new_mask
