from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import tomllib


class ProfileNotFound(Exception):
    """Raised when a named profile cannot be found."""
    pass


def load_profiles(path: Path | None = None) -> Dict[str, Dict[str, Any]]:
    """
    Load all profiles from a TOML file.

    Expected shape:

        [profiles.some-name]
        url = "https://..."
        method = "GET"

    Returns dict: { "some-name": { ... } }
    """
    if path is None:
        path = Path("profiles.toml")

    if not path.exists():
        raise FileNotFoundError(f"No profiles file found at {path!s}")

    data = tomllib.loads(path.read_text(encoding="utf-8"))
    profiles = data.get("profiles", {})
    if not isinstance(profiles, dict):
        raise ValueError("profiles.toml must contain a [profiles] table")

    fixed: Dict[str, Dict[str, Any]] = {}
    for name, cfg in profiles.items():
        if isinstance(cfg, dict):
            fixed[name] = cfg
    return fixed


def load_profile(name: str, path: Path | None = None) -> Dict[str, Any]:
    """
    Load a single profile by name from profiles.toml.
    """
    profiles = load_profiles(path)
    try:
        return profiles[name]
    except KeyError:
        raise ProfileNotFound(f"Profile '{name}' not found in profiles.toml") from None

