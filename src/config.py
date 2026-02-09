"""Load and validate the TOML configuration file."""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

from loguru import logger

from src.models import AppConfig


def load_config(path: str | Path = "config.toml") -> AppConfig:
    """Read *path* and return a validated :class:`AppConfig`.

    Exits with an error message if the file is missing or invalid.
    """
    config_path = Path(path)
    if not config_path.exists():
        logger.error("Configuration file not found: {}", config_path)
        sys.exit(1)

    logger.info("Loading configuration from {}", config_path)
    with config_path.open("rb") as fh:
        raw = tomllib.load(fh)

    try:
        config = AppConfig.model_validate(raw)
    except (ValueError, TypeError) as exc:
        logger.error("Invalid configuration: {}", exc)
        sys.exit(1)

    _validate(config)

    meter_count = sum(len(m.meters) for m in config.members)
    logger.info(
        "Configuration loaded — collective: '{}', {} members, {} meters",
        config.collective.name,
        len(config.members),
        meter_count,
    )
    return config


_SUPPORTED_LANGUAGES = {"en", "de", "fr", "it"}


def _validate(config: AppConfig) -> None:
    """Warn about potential issues in the configuration."""
    lang = config.collective.language.lower().strip()
    if lang not in _SUPPORTED_LANGUAGES:
        logger.warning(
            "Unsupported language '{}' — falling back to English. Supported: {}",
            config.collective.language,
            ", ".join(sorted(_SUPPORTED_LANGUAGES)),
        )

    if config.collective.local_rate == 0.0:
        logger.warning("Collective local_rate is 0 — local solar costs/revenue will be zero")
    if config.collective.bkw_buy_rate == 0.0:
        logger.warning("Collective bkw_buy_rate is 0 — grid costs will be zero")
