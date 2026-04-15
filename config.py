"""Shared config loader for bot.py and jk."""
import os
import tomllib
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent / "config.toml"


def load() -> dict:
    path = Path(os.environ.get("JK_CONFIG", DEFAULT_PATH))
    if not path.exists():
        raise SystemExit(
            f"config not found at {path}. "
            f"Copy config.example.toml to config.toml, or set JK_CONFIG."
        )
    with path.open("rb") as f:
        return tomllib.load(f)
