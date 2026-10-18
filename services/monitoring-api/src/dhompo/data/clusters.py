"""Klaster hidrologis stasiun pada basin."""

from __future__ import annotations

from dhompo.data.loader import TARGET_STATION

UPSTREAM_WEST: tuple[str, ...] = (
    "Bd. Suwoto",
    "Krajan Timur",
    "Purwodadi",
    "Bd. Baong",
    "Bd. Bakalan",
    "AWLR Kademungan",
    "Bd. Domas",
    "Bd. Grinting",
)

UPSTREAM_EAST: tuple[str, ...] = (
    "Bd. Lecari",
    "Bd Guyangan",
    "Sidogiri",
    "Klosod",
)

LOCAL: tuple[str, ...] = (
    TARGET_STATION,
    "Jalan Nasional",
)

CLUSTERS: dict[str, tuple[str, ...]] = {
    "upstream_west": UPSTREAM_WEST,
    "upstream_east": UPSTREAM_EAST,
    "local": LOCAL,
}

TELEMETRY_STATIONS: frozenset[str] = frozenset(
    {"Purwodadi", "AWLR Kademungan", "Klosod", TARGET_STATION}
)

UPSTREAM_TELEMETRY: tuple[str, ...] = ("Purwodadi", "AWLR Kademungan", "Klosod")

PRIMARY_STATION_BY_HORIZON: dict[int, str] = {
    1: "Klosod",
    2: "AWLR Kademungan",
    3: "AWLR Kademungan",
    4: "Purwodadi",
    5: "Purwodadi",
}

TELEMETRY_STALE_SECONDS: int = 30 * 60
OFFLINE_STALE_SECONDS: int = 6 * 60 * 60


def cluster_of(station: str) -> str:
    for name, members in CLUSTERS.items():
        if station in members:
            return name
    raise KeyError(f"Stasiun '{station}' bukan anggota klaster manapun.")


def is_telemetry(station: str) -> bool:
    return station in TELEMETRY_STATIONS
