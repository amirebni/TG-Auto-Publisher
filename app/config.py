"""Config loading and validation."""
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.getenv("TG_CONFIG") or ROOT / "config.yaml")

VALID_TYPES = {"text", "photo", "video", "music", "voice", "document"}

DEFAULT_SETTINGS = {
    # max messages fetched per source per run
    "poll_limit": 100,
    # latest = start from new posts only | from_start = process history
    "first_run_behavior": "latest",
    # true = preview only, nothing is published
    "dry_run": True,
    "log_level": "INFO",
    "state_db": "data/state.sqlite3",
    # ignore posts newer than N seconds (lets albums finish arriving)
    "settle_seconds": 60,
    # failed post is retried N times (across runs), then skipped
    "max_retries": 3,
    # pause between sends (seconds), avoids flood limits
    "send_delay": 1.0,
    # skip files bigger than this
    "max_file_mb": 200,
    # media caption limit (1024 normal, 4096 premium accounts)
    "caption_limit": 1024,
    # FloodWait up to N seconds is waited out, longer stops the run
    "max_flood_wait": 120,
    # stop starting new posts after N seconds (state is still saved)
    "max_runtime_seconds": 480,
    # auto = resend media server-side, fall back to download+upload
    # download = always download+upload
    "media_mode": "auto",
    # processed-post records older than N days are pruned
    "state_keep_days": 30,
}


class ConfigError(RuntimeError):
    pass


def _as_bool(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def validate_source(source, index):
    label = source.get("name") or f"#{index + 1}"

    if not isinstance(source, dict):
        raise ConfigError(f"Source {label}: must be a mapping")

    if not source.get("peer"):
        raise ConfigError(f"Source {label}: 'peer' is required")

    types = source.get("allowed_types")
    if types is not None:
        unknown = set(types) - VALID_TYPES
        if unknown:
            raise ConfigError(
                f"Source {label}: unknown allowed_types {sorted(unknown)}; "
                f"valid: {sorted(VALID_TYPES)}"
            )

    routes = source.get("routes") or {}
    if not isinstance(routes, dict):
        raise ConfigError(f"Source {label}: 'routes' must be a mapping")

    unknown_routes = set(routes) - VALID_TYPES - {"default"}
    if unknown_routes:
        raise ConfigError(
            f"Source {label}: unknown route keys {sorted(unknown_routes)}"
        )

    has_destination = bool(source.get("destinations")) or any(routes.values())
    if not has_destination:
        raise ConfigError(
            f"Source {label}: set 'destinations' or 'routes'"
        )


def load_config(path=None):
    path = Path(path) if path else CONFIG_PATH

    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    if not isinstance(raw, dict):
        raise ConfigError("config.yaml must be a mapping")

    settings = dict(DEFAULT_SETTINGS)
    settings.update(raw.get("settings") or {})

    settings["dry_run"] = _as_bool(settings["dry_run"])

    # workflow_dispatch input can override dry_run
    override = os.getenv("DRY_RUN", "").strip().lower()
    if override in ("true", "false"):
        settings["dry_run"] = override == "true"

    if settings["first_run_behavior"] not in ("latest", "from_start"):
        raise ConfigError("first_run_behavior must be 'latest' or 'from_start'")

    if settings["media_mode"] not in ("auto", "download"):
        raise ConfigError("media_mode must be 'auto' or 'download'")

    state_db = Path(settings["state_db"])
    if not state_db.is_absolute():
        state_db = ROOT / state_db
    settings["state_db"] = str(state_db)

    sources = raw.get("sources") or []
    if not isinstance(sources, list):
        raise ConfigError("'sources' must be a list")

    for i, source in enumerate(sources):
        if isinstance(source, dict) and not source.get("enabled", True):
            continue
        validate_source(source, i)

    return {"settings": settings, "sources": sources}


def get_telegram_credentials():
    api_id = os.getenv("TG_API_ID", "").strip()
    api_hash = os.getenv("TG_API_HASH", "").strip()
    session = os.getenv("TG_SESSION", "").strip()

    if not api_id or not api_hash or not session:
        raise RuntimeError(
            "Missing Telegram credentials "
            "(TG_API_ID / TG_API_HASH / TG_SESSION). "
            "Check GitHub Actions Secrets."
        )

    try:
        api_id = int(api_id)
    except ValueError:
        raise RuntimeError("TG_API_ID must be a number")

    return api_id, api_hash, session
