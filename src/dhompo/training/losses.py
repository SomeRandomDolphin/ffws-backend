"""Loss komposit peak-weighted + auxiliary-head untuk training Tier-A."""

from __future__ import annotations

from dataclasses import dataclass

try:
    import torch
    import torch.nn as nn
except ImportError as exc:
    raise ImportError(
        "Install via: pip install -r requirements-torch.txt"
    ) from exc


@dataclass(frozen=True)
class LossConfig:
    aux_weight: float = 0.1
    elevated_threshold: float = 7.0
    flood_threshold: float = 9.0
    elevated_weight: float = 3.0
    flood_weight: float = 10.0


def peak_weighted_mse(
    pred: torch.Tensor,
    target: torch.Tensor,
    config: LossConfig | None = None,
) -> torch.Tensor:
    """MSE berbobot peak: bobot 1 normal, 3 elevated, 10 banjir."""
    if config is None:
        config = LossConfig()
    sq = (pred - target) ** 2
    w = torch.ones_like(target)
    w = torch.where(target >= config.elevated_threshold,
                    torch.full_like(w, config.elevated_weight), w)
    w = torch.where(target >= config.flood_threshold,
                    torch.full_like(w, config.flood_weight), w)
    return (w * sq).mean()


class CompositeLoss(nn.Module):
    """Jumlah loss utama (peak-weighted) dan loss auxiliary stasiun tanpa bobot."""

    def __init__(self, config: LossConfig | None = None) -> None:
        super().__init__()
        self.config = config or LossConfig()

    def forward(
        self,
        horizon_pred: torch.Tensor,
        horizon_target: torch.Tensor,
        aux_pred: torch.Tensor,
        aux_target: torch.Tensor,
        aux_mask: torch.Tensor | None = None,
    ) -> dict[str, torch.Tensor]:
        main = peak_weighted_mse(horizon_pred, horizon_target, self.config)

        aux_sq = (aux_pred - aux_target) ** 2
        if aux_mask is not None:
            aux_mask_f = aux_mask.float()
            denom = aux_mask_f.sum().clamp(min=1.0)
            aux = (aux_sq * aux_mask_f).sum() / denom
        else:
            aux = aux_sq.mean()

        total = main + self.config.aux_weight * aux
        return {"total": total, "main": main, "aux": aux}
