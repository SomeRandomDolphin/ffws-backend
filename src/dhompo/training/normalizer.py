"""Normalizer z-score untuk fitur per-stasiun dan tensor AR lag Tier-A."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

try:
    import torch
except ImportError as exc:
    raise ImportError(
        "Install via: pip install -r requirements-torch.txt"
    ) from exc


@dataclass
class Normalizer:
    mean_feats: np.ndarray   # (n_stations, features_per_station)
    std_feats: np.ndarray
    mean_ar: np.ndarray      # (ar_lag_dim,)
    std_ar: np.ndarray

    @classmethod
    def fit(
        cls, feats: torch.Tensor, ar: torch.Tensor, eps: float = 1e-6,
    ) -> "Normalizer":
        f = feats.numpy()
        a = ar.numpy()
        mean_feats = f.mean(axis=0)
        std_feats = f.std(axis=0).clip(min=eps)
        mean_ar = a.mean(axis=0)
        std_ar = a.std(axis=0).clip(min=eps)
        return cls(mean_feats, std_feats, mean_ar, std_ar)

    def apply(
        self, feats: torch.Tensor, ar: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        mf = torch.from_numpy(self.mean_feats).to(feats)
        sf = torch.from_numpy(self.std_feats).to(feats)
        ma = torch.from_numpy(self.mean_ar).to(ar)
        sa = torch.from_numpy(self.std_ar).to(ar)
        return (feats - mf) / sf, (ar - ma) / sa
