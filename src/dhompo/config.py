from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_yaml_config(path: str | Path) -> dict:
    """Load YAML config relative to project root when needed."""
    p = Path(path)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def resolve_artifact_path(model_dir: str | Path, value: str | Path) -> Path:
    """Resolve model metadata paths, including artifacts moved from Windows.

    Existing paths keep their meaning. For a stale absolute path recorded by
    training, use the same filename inside the selected model directory.
    """
    artifact = Path(value)
    windows_path = PureWindowsPath(value)
    if artifact.is_absolute() or windows_path.is_absolute() or PurePosixPath(value).is_absolute():
        if artifact.exists():
            return artifact
        return Path(model_dir) / windows_path.name
    return Path(model_dir) / artifact


def resolve_path_from_config(config_path: str | Path, value: str | Path | None) -> Path | None:
    """Resolve a nested path relative to the config file location."""
    if value is None:
        return None

    resolved = Path(value)
    if resolved.is_absolute():
        return resolved

    cfg_path = Path(config_path)
    if not cfg_path.is_absolute():
        cfg_path = PROJECT_ROOT / cfg_path
    return (cfg_path.parent / resolved).resolve()


def load_serving_config() -> dict:
    return load_yaml_config("configs/dhompo/serving.yaml")
