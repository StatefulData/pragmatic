"""Configuration loader.

Precedence (highest first):
    1. Values explicitly overridden by the caller (e.g. CLI flags)
    2. Environment variables
    3. config.toml
    4. Dataclass defaults
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field, fields
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib


@dataclass
class SnowflakeConfig:
    account: str = ""
    user: str = ""
    role: str = ""
    warehouse: str = ""
    database: str = ""
    schema: str = ""
    private_key_path: str = ""
    private_key_passphrase: str = ""
    password: str = ""

    def validate(self) -> None:
        required = ["account", "user", "warehouse", "database", "schema"]
        missing = [f for f in required if not getattr(self, f)]
        if missing:
            raise ValueError(f"Missing required Snowflake config: {missing}")
        if not self.private_key_path and not self.password:
            raise ValueError("Either private_key_path or password must be set")


@dataclass
class TargetConfig:
    table: str = "OBSERVABILITY.DATA.QUERY_OPERATOR_STATS_HISTORY"
    stage: str = "OBSERVABILITY.DATA.QUERY_OPERATOR_STATS_STAGE"


@dataclass
class PipelineConfig:
    lookback_hours: int = 4
    min_execute_seconds: int = 60
    workers: int = 8
    max_queries_per_run: int = 100000
    scratch_dir: str = "/tmp/query_operator_stats"
    log_level: str = "INFO"


@dataclass
class Config:
    snowflake: SnowflakeConfig = field(default_factory=SnowflakeConfig)
    target: TargetConfig = field(default_factory=TargetConfig)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)


# Mapping of (section, field) → environment variable name.
# Kept as an explicit table rather than reflection so that config.example.toml
# documentation stays authoritative.
_ENV_MAP: dict[tuple[str, str], str] = {
    ("snowflake", "account"): "SNOWFLAKE_ACCOUNT",
    ("snowflake", "user"): "SNOWFLAKE_USER",
    ("snowflake", "role"): "SNOWFLAKE_ROLE",
    ("snowflake", "warehouse"): "SNOWFLAKE_WAREHOUSE",
    ("snowflake", "database"): "SNOWFLAKE_DATABASE",
    ("snowflake", "schema"): "SNOWFLAKE_SCHEMA",
    ("snowflake", "private_key_path"): "SNOWFLAKE_PRIVATE_KEY_PATH",
    ("snowflake", "private_key_passphrase"): "SNOWFLAKE_PRIVATE_KEY_PASSPHRASE",
    ("snowflake", "password"): "SNOWFLAKE_PASSWORD",
    ("target", "table"): "QOS_TARGET_TABLE",
    ("target", "stage"): "QOS_TARGET_STAGE",
    ("pipeline", "lookback_hours"): "QOS_LOOKBACK_HOURS",
    ("pipeline", "min_execute_seconds"): "QOS_MIN_EXECUTE_SECONDS",
    ("pipeline", "workers"): "QOS_WORKERS",
    ("pipeline", "max_queries_per_run"): "QOS_MAX_QUERIES_PER_RUN",
    ("pipeline", "scratch_dir"): "QOS_SCRATCH_DIR",
    ("pipeline", "log_level"): "QOS_LOG_LEVEL",
}

_INT_FIELDS = {
    "lookback_hours",
    "min_execute_seconds",
    "workers",
    "max_queries_per_run",
}


def _apply_section(section_obj: object, values: dict, section_name: str) -> None:
    """Apply a dict of overrides to a config section, with env overriding TOML."""
    for f in fields(section_obj):  # type: ignore[arg-type]
        # 1. TOML value (if present)
        if f.name in values:
            setattr(section_obj, f.name, values[f.name])
        # 2. env var overrides TOML
        env_key = _ENV_MAP.get((section_name, f.name))
        if env_key and env_key in os.environ:
            raw = os.environ[env_key]
            setattr(section_obj, f.name, int(raw) if f.name in _INT_FIELDS else raw)


def load_config(config_path: str | Path | None = None) -> Config:
    """Load config from a TOML file + environment variables.

    If `config_path` is None, looks for ./config/config.toml relative to cwd,
    then config/config.toml in the package's parent. If neither exists, loads
    from env + defaults only — which is fine for containerized deployments.
    """
    cfg = Config()
    data: dict = {}

    candidate_paths: list[Path] = []
    if config_path is not None:
        candidate_paths.append(Path(config_path))
    else:
        candidate_paths.extend([
            Path.cwd() / "config" / "config.toml",
            Path(__file__).resolve().parents[2] / "config" / "config.toml",
        ])

    for p in candidate_paths:
        if p.is_file():
            with open(p, "rb") as fh:
                data = tomllib.load(fh)
            break

    _apply_section(cfg.snowflake, data.get("snowflake", {}), "snowflake")
    _apply_section(cfg.target, data.get("target", {}), "target")
    _apply_section(cfg.pipeline, data.get("pipeline", {}), "pipeline")

    cfg.snowflake.validate()
    return cfg
