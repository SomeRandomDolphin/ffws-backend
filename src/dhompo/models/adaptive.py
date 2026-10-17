"""Model adaptive Tier-A: shared station embedding, agregasi klaster, head multi-task."""

from __future__ import annotations

from dataclasses import dataclass

try:
    import torch
    import torch.nn as nn
except ImportError as exc:
    raise ImportError(
        "Install via: pip install -r requirements-torch.txt"
    ) from exc

from dhompo.data.clusters import CLUSTERS
from dhompo.data.loader import ALL_STATIONS, TARGET_STATION


@dataclass(frozen=True)
class AdaptiveTierAConfig:
    features_per_station: int = 7
    embedding_dim: int = 8
    ar_lag_dim: int = 6
    horizon_count: int = 5
    hidden_dim: int = 64
    dropout_p: float = 0.1
    stations: tuple[str, ...] = tuple(ALL_STATIONS)
    cluster_order: tuple[str, ...] = ("upstream_west", "upstream_east", "local")


def _cluster_index_map(
    stations: tuple[str, ...], cluster_order: tuple[str, ...]
) -> dict[str, list[int]]:
    """Petakan nama klaster → daftar terurut indeks stasiun anggotanya."""
    out: dict[str, list[int]] = {name: [] for name in cluster_order}
    for idx, station in enumerate(stations):
        for name in cluster_order:
            if station in CLUSTERS[name]:
                out[name].append(idx)
                break
    return out


class StationEmbedding(nn.Module):
    """Embedding dense yang dipakai bersama untuk setiap stasiun."""

    def __init__(self, in_features: int, out_features: int) -> None:
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(in_features, out_features),
            nn.GELU(),
            nn.Linear(out_features, out_features),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


class ClusterAggregator(nn.Module):
    """Agregasi masked-mean per klaster hidrologis."""

    def __init__(self, cluster_indices: dict[str, list[int]]) -> None:
        super().__init__()
        self._cluster_indices = cluster_indices
        self._cluster_order = list(cluster_indices.keys())

    def forward(
        self, embeddings: torch.Tensor, mask: torch.Tensor
    ) -> torch.Tensor:
        cluster_vecs = []
        for name in self._cluster_order:
            idx = self._cluster_indices[name]
            if not idx:
                cluster_vecs.append(torch.zeros(
                    embeddings.shape[0], embeddings.shape[-1],
                    device=embeddings.device, dtype=embeddings.dtype,
                ))
                continue
            cluster_emb = embeddings[:, idx, :]
            cluster_mask = mask[:, idx].unsqueeze(-1).float()
            weighted = cluster_emb * cluster_mask
            denom = cluster_mask.sum(dim=1).clamp(min=1.0)
            pooled = weighted.sum(dim=1) / denom
            cluster_vecs.append(pooled)
        return torch.cat(cluster_vecs, dim=-1)


class AdaptiveTierA(nn.Module):
    """Model adaptive 14 stasiun dengan head auxiliary multi-task."""

    def __init__(self, config: AdaptiveTierAConfig | None = None) -> None:
        super().__init__()
        self.config = config or AdaptiveTierAConfig()
        cfg = self.config
        n_stations = len(cfg.stations)

        # +1 untuk bit mask yang di-concat ke fitur setiap stasiun.
        self.embedding = StationEmbedding(
            in_features=cfg.features_per_station + 1,
            out_features=cfg.embedding_dim,
        )

        cluster_indices = _cluster_index_map(cfg.stations, cfg.cluster_order)
        self._cluster_indices = cluster_indices
        self.aggregator = ClusterAggregator(cluster_indices)

        merged_dim = (
            cfg.embedding_dim * len(cfg.cluster_order) + cfg.ar_lag_dim
        )
        self.trunk = nn.Sequential(
            nn.Linear(merged_dim, cfg.hidden_dim),
            nn.GELU(),
            nn.Dropout(cfg.dropout_p),
            nn.Linear(cfg.hidden_dim, cfg.hidden_dim),
            nn.GELU(),
            nn.Dropout(cfg.dropout_p),
        )

        self.horizon_heads = nn.ModuleList(
            [nn.Linear(cfg.hidden_dim, 1) for _ in range(cfg.horizon_count)]
        )
        self.aux_heads = nn.ModuleList(
            [nn.Linear(cfg.embedding_dim, 1) for _ in range(n_stations)]
        )

    @property
    def target_station_index(self) -> int:
        return self.config.stations.index(TARGET_STATION)

    def forward(
        self,
        station_features: torch.Tensor,
        mask: torch.Tensor,
        ar_lags: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        if station_features.dim() != 3:
            raise ValueError(
                f"station_features harus 3-D (batch, n_stations, F); "
                f"diterima shape {tuple(station_features.shape)}."
            )
        mask_f = mask.float().unsqueeze(-1)
        # Nolkan fitur stasiun yang ter-mask; bit mask-nya sendiri tetap 1.
        feature_part = station_features * mask_f
        x = torch.cat([feature_part, mask_f], dim=-1)

        embeddings = self.embedding(x)
        cluster_vec = self.aggregator(embeddings, mask)

        merged = torch.cat([cluster_vec, ar_lags], dim=-1)
        trunk = self.trunk(merged)

        horizons = torch.cat(
            [head(trunk) for head in self.horizon_heads], dim=-1,
        )
        aux = torch.cat(
            [head(embeddings[:, i, :]) for i, head in enumerate(self.aux_heads)],
            dim=-1,
        )
        return horizons, aux
