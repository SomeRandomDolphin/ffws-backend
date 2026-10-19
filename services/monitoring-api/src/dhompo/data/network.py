"""Graf jaringan sungai 15 stasiun DAS Welang untuk simulator hulu-hilir.

Modul ini memuat ``configs/dhompo/network.yaml`` dan menyediakan operasi
graf sederhana (tetangga hulu/hilir, urutan topologis, jalur ke target) yang
dipakai oleh simulator routing dan model multi-stasiun.

Data acuan 2022/2023 adalah keluaran model; konfigurasi jaringan adalah
asumsi eksplisit dan divalidasi saat pemuatan (semua node mengalir ke target
atau ke stasiun aux hilir, tanpa siklus).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from dhompo.config import PROJECT_ROOT

NETWORK_PATH = PROJECT_ROOT / "configs" / "dhompo" / "network.yaml"


@dataclass(frozen=True)
class StationNode:
    """Metadata satu stasiun pada graf jaringan."""

    name: str
    elevation_m: float
    travel_hours_to_target: float
    branch: str
    role: str


@dataclass(frozen=True)
class Reach:
    """Satu reach hulu -> hilir dengan waktu tempuh (jam)."""

    upstream: str
    downstream: str
    travel_hours: float


@dataclass(frozen=True)
class RiverNetwork:
    """Graf jaringan sungai lengkap beserta operasi turunannya."""

    target: str
    downstream_aux: str
    nodes: tuple[StationNode, ...]
    reaches: tuple[Reach, ...]
    defaults: dict

    @property
    def station_names(self) -> list[str]:
        return [node.name for node in self.nodes]

    @property
    def node(self) -> dict[str, StationNode]:
        return {n.name: n for n in self.nodes}

    @property
    def edges(self) -> list[tuple[str, str]]:
        return [(r.upstream, r.downstream) for r in self.reaches]

    def upstream_of(self, station: str) -> list[str]:
        """Daftar stasiun yang mengalir langsung ke ``station``."""
        return [r.upstream for r in self.reaches if r.downstream == station]

    def downstream_of(self, station: str) -> list[str]:
        """Daftar stasiun penerima aliran langsung dari ``station``."""
        return [r.downstream for r in self.reaches if r.upstream == station]

    def travel_hours(self, upstream: str, downstream: str) -> float:
        for r in self.reaches:
            if r.upstream == upstream and r.downstream == downstream:
                return r.travel_hours
        raise KeyError(f"Tidak ada reach {upstream!r} -> {downstream!r}.")

    def order_upstream_to_downstream(self) -> list[str]:
        """Urutan topologis node: seluruh hulu muncul sebelum hilirnya."""
        indegree = {n.name: 0 for n in self.nodes}
        for r in self.reaches:
            indegree[r.downstream] += 1
        queue = [name for name, deg in indegree.items() if deg == 0]
        order: list[str] = []
        while queue:
            name = queue.pop(0)
            order.append(name)
            for nxt in self.downstream_of(name):
                indegree[nxt] -= 1
                if indegree[nxt] == 0:
                    queue.append(nxt)
        return order

    def path_to_target(self, station: str) -> list[str]:
        """Jalur hilir dari ``station`` sampai target (inklusif), bila ada."""
        path = [station]
        seen = {station}
        current = station
        while current != self.target:
            nxt = self.downstream_of(current)
            if not nxt:
                return []
            current = nxt[0]
            if current in seen:
                return []
            seen.add(current)
            path.append(current)
        return path

    def reachable(self, station: str) -> bool:
        return bool(self.path_to_target(station))

    def validate(self) -> None:
        """Pastikan graf konsisten; lempar ``ValueError`` bila tidak."""
        names = self.station_names
        if len(names) != len(set(names)):
            raise ValueError("Nama stasiun duplikat pada network.yaml.")

        known = set(names)
        if self.target not in known:
            raise ValueError(f"Target {self.target!r} tidak ada di daftar stasiun.")
        if self.downstream_aux not in known:
            raise ValueError(
                f"downstream_aux {self.downstream_aux!r} tidak ada di daftar stasiun."
            )

        for r in self.reaches:
            if r.upstream not in known or r.downstream not in known:
                raise ValueError(
                    f"Reach tidak valid: {r.upstream!r} -> {r.downstream!r}."
                )
            if r.travel_hours <= 0:
                raise ValueError(
                    f"travel_hours harus > 0 pada reach {r.upstream!r} -> "
                    f"{r.downstream!r}."
                )

        if len(self.order_upstream_to_downstream()) != len(names):
            raise ValueError("Graf mengandung siklus (topological sort tidak lengkap).")

        for name in names:
            if name in (self.target, self.downstream_aux):
                continue
            if not self.reachable(name):
                raise ValueError(f"Stasiun {name!r} tidak mengalir ke target.")


def load_network(path: str | Path | None = None) -> RiverNetwork:
    """Muat dan validasi graf jaringan dari ``network.yaml``."""
    cfg_path = Path(path) if path else NETWORK_PATH
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

    stations = tuple(
        StationNode(
            name=str(item["name"]),
            elevation_m=float(item["elevation_m"]),
            travel_hours_to_target=float(item["travel_hours_to_target"]),
            branch=str(item.get("branch", "")),
            role=str(item.get("role", "")),
        )
        for item in raw.get("stations", [])
    )
    reaches = tuple(
        Reach(
            upstream=str(item["upstream"]),
            downstream=str(item["downstream"]),
            travel_hours=float(item["travel_hours"]),
        )
        for item in raw.get("edges", [])
    )

    network = RiverNetwork(
        target=str(raw["target"]),
        downstream_aux=str(raw["downstream_aux"]),
        nodes=stations,
        reaches=reaches,
        defaults=dict(raw.get("defaults", {})),
    )
    network.validate()
    return network


__all__ = [
    "NETWORK_PATH",
    "Reach",
    "RiverNetwork",
    "StationNode",
    "load_network",
]
