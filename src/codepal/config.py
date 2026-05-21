"""Config loader: TOML file + CODEPAL_* env var overrides."""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef]


# ---------------------------------------------------------------------------
# Pydantic settings models
# ---------------------------------------------------------------------------


class ServerConfig(BaseSettings):
    host: str = "127.0.0.1"
    port: int = 8742
    log_level: str = "info"


class OllamaConfig(BaseSettings):
    base_url: str = "http://localhost:11434"
    embed_model: str = "nomic-embed-text"
    chat_model: str = "qwen3:14b"
    embed_timeout: int = 60
    chat_timeout: int = 120


class ChromaConfig(BaseSettings):
    persist_dir: str = "~/.codepal/chroma"


class IndexerConfig(BaseSettings):
    state_db: str = "~/.codepal/index_state.db"
    chunk_token_budget: int = 512
    chunk_overlap: int = 50


class DispatcherConfig(BaseSettings):
    bug_score_threshold: float = 0.85
    local_llm_score_threshold: float = 0.60


class ExternalLLMConfig(BaseSettings):
    api_key: Optional[str] = None
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o"


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CODEPAL_",
        env_nested_delimiter="__",
        case_sensitive=False,
    )

    server: ServerConfig = Field(default_factory=ServerConfig)
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)
    chroma: ChromaConfig = Field(default_factory=ChromaConfig)
    indexer: IndexerConfig = Field(default_factory=IndexerConfig)
    dispatcher: DispatcherConfig = Field(default_factory=DispatcherConfig)
    external_llm: ExternalLLMConfig = Field(default_factory=ExternalLLMConfig)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG_PATHS = [
    Path.home() / ".codepal" / "codepal.toml",
    Path("codepal.toml"),
    Path("config.toml"),
]


def _load_toml(path: Path) -> dict:
    """Load a TOML file; return empty dict if not found."""
    if not path.exists():
        return {}
    with path.open("rb") as fh:
        return tomllib.load(fh)


def load_config(config_path: Optional[Path] = None) -> AppConfig:
    """Load config from TOML file, then apply env var overrides."""
    raw: dict = {}

    if config_path:
        raw = _load_toml(config_path)
    else:
        for candidate in _DEFAULT_CONFIG_PATHS:
            data = _load_toml(candidate)
            if data:
                raw = data
                break

    # Convert nested TOML sections into the AppConfig structure
    return AppConfig.model_validate(raw)


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    """Return the cached global config instance."""
    return load_config()
