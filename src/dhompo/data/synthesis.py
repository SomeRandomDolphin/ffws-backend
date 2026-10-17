"""Augmenter sintetis berskala basin untuk training Tier-A."""

from __future__ import annotations

from dataclasses import dataclass, field

try:
    import torch
except ImportError as exc:
    raise ImportError(
        "Install via: pip install -r requirements-torch.txt"
    ) from exc


_VALUE_CHANNEL: int = 0


@dataclass(frozen=True)
class AugmentationSchedule:
    """Probabilitas penerapan augmentasi per regime."""

    normal: float = 0.30
    elevated: float = 0.50
    flood: float = 0.70


@dataclass(frozen=True)
class AugmentationConfig:
    """Parameter jitter dan magnitude scaling untuk augmenter."""

    jitter_sigma_pct: float = 0.005
    scale_low: float = 0.90
    scale_high: float = 1.15
    min_plausible: float = 0.0
    # Banjir sintetis bisa wajar melebihi maksimum historis, jadi batas atas
    # dipakai sebagai pengali training_max, bukan cap absolut
    max_plausible_ratio: float = 1.5
    elevated_threshold: float = 7.0
    flood_threshold: float = 9.0


@dataclass
class AugmentationStats:
    """Hitungan augmentasi dan alasan penolakan per batch."""

    n_total: int = 0
    n_attempted: int = 0
    n_rejected: int = 0
    rejection_reasons: dict[str, int] = field(default_factory=dict)

    def rejection_rate(self) -> float:
        if self.n_attempted == 0:
            return 0.0
        return self.n_rejected / self.n_attempted


def regime_indices(
    target_value: torch.Tensor,
    config: AugmentationConfig,
) -> torch.Tensor:
    """Petakan tensor (batch,) tinggi muka air Dhompo ke indeks regime 0/1/2."""
    regime = torch.zeros_like(target_value, dtype=torch.long)
    regime = torch.where(
        target_value >= config.elevated_threshold,
        torch.ones_like(regime), regime,
    )
    regime = torch.where(
        target_value >= config.flood_threshold,
        torch.full_like(regime, 2), regime,
    )
    return regime


class SyntheticAugmenter:
    """Augmenter berskala basin yang diterapkan per batch training."""

    def __init__(
        self,
        station_std: torch.Tensor,
        training_max: torch.Tensor,
        schedule: AugmentationSchedule | None = None,
        config: AugmentationConfig | None = None,
        rng: torch.Generator | None = None,
    ) -> None:
        if station_std.dim() != 1:
            raise ValueError(
                f"station_std must be 1-D (n_stations,); got {tuple(station_std.shape)}."
            )
        if training_max.shape != station_std.shape:
            raise ValueError(
                "training_max and station_std must share the same shape."
            )
        self._station_std = station_std
        self._training_max = training_max
        self._schedule = schedule or AugmentationSchedule()
        self._config = config or AugmentationConfig()
        self._rng = rng

    @property
    def config(self) -> AugmentationConfig:
        return self._config

    def tighten(self, factor: float = 0.2) -> AugmentationConfig:
        """Perketat jitter sigma dan rentang skala sebesar ``factor``."""
        scale_mid = 0.5 * (self._config.scale_low + self._config.scale_high)
        scale_half_range = 0.5 * (self._config.scale_high - self._config.scale_low)
        new_half = scale_half_range * (1.0 - factor)
        new_config = AugmentationConfig(
            jitter_sigma_pct=self._config.jitter_sigma_pct * (1.0 - factor),
            scale_low=scale_mid - new_half,
            scale_high=scale_mid + new_half,
            min_plausible=self._config.min_plausible,
            max_plausible_ratio=self._config.max_plausible_ratio,
            elevated_threshold=self._config.elevated_threshold,
            flood_threshold=self._config.flood_threshold,
        )
        self._config = new_config
        return new_config

    def _coin(
        self, shape: tuple[int, ...], device: torch.device, dtype: torch.dtype,
    ) -> torch.Tensor:
        if self._rng is None:
            return torch.rand(shape, device=device, dtype=dtype)
        coin = torch.empty(shape, device=device, dtype=dtype)
        coin.uniform_(0.0, 1.0, generator=self._rng)
        return coin

    def _normal(
        self, shape: tuple[int, ...], device: torch.device, dtype: torch.dtype,
    ) -> torch.Tensor:
        if self._rng is None:
            return torch.randn(shape, device=device, dtype=dtype)
        out = torch.empty(shape, device=device, dtype=dtype)
        out.normal_(mean=0.0, std=1.0, generator=self._rng)
        return out

    def augment(
        self,
        feats_raw: torch.Tensor,
        ar_lags_raw: torch.Tensor,
        target_value: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, AugmentationStats]:
        """Augmentasi tensor fitur per-stasiun mentah; kembalikan kept mask dan stats."""
        if feats_raw.dim() != 3:
            raise ValueError(
                f"feats_raw must be 3-D (B, S, F); got {tuple(feats_raw.shape)}."
            )

        device = feats_raw.device
        dtype = feats_raw.dtype
        batch_size = feats_raw.shape[0]

        regime = regime_indices(target_value, self._config)
        p_table = torch.tensor(
            [self._schedule.normal, self._schedule.elevated, self._schedule.flood],
            device=device, dtype=dtype,
        )
        p_aug = p_table[regime]                                  # (B,)
        coin = self._coin((batch_size,), device, dtype)
        apply_aug = coin < p_aug                                 # (B,)

        feats_aug = feats_raw.clone()
        ar_aug = ar_lags_raw.clone()
        stats = AugmentationStats(n_total=batch_size)

        if not bool(apply_aug.any()):
            return feats_aug, ar_aug, torch.ones(batch_size, dtype=torch.bool,
                                                  device=device), stats

        stats.n_attempted = int(apply_aug.sum().item())

        scale = self._sample_scale(batch_size, regime, device, dtype)
        scale = torch.where(
            apply_aug, scale, torch.ones_like(scale),
        )                                                        # (B,)
        feats_aug[..., _VALUE_CHANNEL] = (
            feats_aug[..., _VALUE_CHANNEL] * scale.unsqueeze(-1)
        )
        ar_aug = ar_aug * scale.unsqueeze(-1)

        jitter = self._normal(
            (batch_size, feats_aug.shape[1]), device, dtype,
        ) * self._station_std.to(device=device, dtype=dtype) * self._config.jitter_sigma_pct
        jitter = jitter * apply_aug.unsqueeze(-1).to(dtype)
        feats_aug[..., _VALUE_CHANNEL] = feats_aug[..., _VALUE_CHANNEL] + jitter

        kept_mask = self._validity_gates(feats_aug, ar_aug, stats)
        kept_mask = kept_mask | (~apply_aug)                     # tolak hanya yang sempat diaugmentasi

        revert = ~kept_mask
        if bool(revert.any()):
            feats_aug[revert] = feats_raw[revert]
            ar_aug[revert] = ar_lags_raw[revert]

        return feats_aug, ar_aug, kept_mask, stats

    def _sample_scale(
        self,
        batch_size: int,
        regime: torch.Tensor,
        device: torch.device,
        dtype: torch.dtype,
    ) -> torch.Tensor:
        u = self._coin((batch_size,), device, dtype)
        low = self._config.scale_low
        high = self._config.scale_high
        # Bias asimetris untuk sample banjir: draw di atas 1.0 supaya peak terangkat.
        flood_mask = regime == 2
        scale = low + (high - low) * u
        flood_scale = 1.0 + (high - 1.0) * u
        return torch.where(flood_mask, flood_scale, scale)

    def _validity_gates(
        self,
        feats_aug: torch.Tensor,
        ar_aug: torch.Tensor,
        stats: AugmentationStats,
    ) -> torch.Tensor:
        """Kembalikan bool mask (batch,); False menandai sample yang ditolak."""
        values = feats_aug[..., _VALUE_CHANNEL]                  # (B, S)
        ratio = self._config.max_plausible_ratio
        upper_bound = self._training_max.to(values) * ratio       # (S,)
        below = (values < self._config.min_plausible).any(dim=-1)
        above = (values > upper_bound).any(dim=-1)
        ar_below = (ar_aug < self._config.min_plausible).any(dim=-1)
        # AR khusus stasiun target, pakai batas training_max target.
        ar_upper = self._training_max.max().to(ar_aug) * ratio
        ar_above = (ar_aug > ar_upper).any(dim=-1)
        bad = below | above | ar_below | ar_above
        if bool(bad.any()):
            stats.n_rejected = int(bad.sum().item())
            stats.rejection_reasons["value_below_min"] = int(below.sum().item())
            stats.rejection_reasons["value_above_max"] = int(above.sum().item())
            stats.rejection_reasons["ar_below_min"] = int(ar_below.sum().item())
            stats.rejection_reasons["ar_above_max"] = int(ar_above.sum().item())
        return ~bad
