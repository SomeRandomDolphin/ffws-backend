import csv
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")

def live_config():
    with (ROOT / "configs/surabaya/live_sources.yaml").open(encoding="utf-8") as file:
        return yaml.safe_load(file)

def stations():
    items = []
    with (ROOT / "configs/dhompo/station_geo.csv").open(encoding="utf-8") as file:
        for row in csv.DictReader(file):
            items.append({"id": "welang-" + slug(row["station"]), "name": row["station"],
                          "region": "welang", "kind": "water_level",
                          "latitude": float(row["latitude"]), "longitude": float(row["longitude"])})
    for loc in live_config()["locations"]:
        coords = loc.get("coordinates", {})
        items.append({"id": loc["key"], "name": loc["name"], "region": "surabaya",
                      "kind": "water_level", "latitude": coords.get("latitude"),
                      "longitude": coords.get("longitude"),
                      "referenceCm": loc["water_level"].get("reference_cm")})
    for gauge in ("Cendono", "Lawang"):
        items.append({"id": "welang-rain-" + gauge.lower(), "name": gauge, "region": "welang",
                      "kind": "rainfall", "latitude": None, "longitude": None})
    return items

def get_station(station_id):
    return next((s for s in stations() if s["id"] == station_id), None)

def measurement(station_id, sensor):
    if station_id.startswith("welang-"):
        return {"metric": "water_level", "unit": "m", "referenceCm": None, "verification": "confirmed_by_owner"}
    for loc in live_config()["locations"]:
        if loc["key"] == station_id and sensor in loc["water_level"]["columns"]:
            spec = loc["water_level"]
            return {"metric": spec.get("archive_metric") or ("distance" if spec.get("measurement") == "distance_to_surface" else "water_level"),
                    "measurement": spec.get("measurement", "water_level"),
                    "unit": "cm", "referenceCm": spec.get("reference_cm"), "verification": "unverified"}
    return {"metric": sensor, "unit": "unverified", "referenceCm": None, "verification": "unverified"}
