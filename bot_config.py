from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from openai import OpenAI

# ── 定数・環境変数 ─────────────────────────────────

ROOT = Path(__file__).parent

_TRUE_ENV_VALUES = {"1", "true", "yes", "on"}


def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in _TRUE_ENV_VALUES


def root_relative_path(env_name: str, default_name: str) -> Path:
    path = Path(os.getenv(env_name, ROOT / default_name))
    return path if path.is_absolute() else ROOT / path


CONFIG_FILE = root_relative_path("CONFIG_FILE", "config.yaml")
SECRETS_FILE = root_relative_path("SECRETS_FILE", "secrets.yaml")
POSTED_FILE = root_relative_path("POSTED_FILE", "posted_entries.txt")

DRY_RUN = env_flag("DRY_RUN")
DRY_RUN_SUMMARIZE = env_flag("DRY_RUN_SUMMARIZE")
DRY_RUN_CLASSIFY = env_flag("DRY_RUN_CLASSIFY")
OVERRIDE_SLACK_CHANNEL_ID = os.getenv("OVERRIDE_SLACK_CHANNEL_ID", "").strip()

# ── YAML ローダ ────────────────────────────────────


def load_mapping(path: Path, *, example_name: str | None = None) -> dict:
    if not path.exists():
        hint = f" Copy {example_name} to {path.name} first." if example_name else ""
        raise RuntimeError(f"Configuration file not found: {path}.{hint}")

    try:
        with path.open(encoding="utf-8") as fp:
            value = yaml.safe_load(fp) or {}
    except yaml.YAMLError as exc:
        raise RuntimeError(f"Invalid YAML in {path}: {exc}") from exc

    if not isinstance(value, dict):
        raise RuntimeError(f"Expected a YAML mapping at the top of {path}")
    return value


config = load_mapping(CONFIG_FILE)
secrets = load_mapping(SECRETS_FILE, example_name="secrets.example.yaml")

try:
    TZ_TOKYO = ZoneInfo(
        str(os.getenv("BOT_TIMEZONE") or config.get("timezone") or "Asia/Tokyo")
    )
except Exception as exc:
    raise RuntimeError(
        "Invalid timezone. Use an IANA name such as Asia/Tokyo or UTC."
    ) from exc

# ── 共通オブジェクト ────────────────────────────────

openai_api_key = str(secrets.get("openai_api_key") or "").strip()
if not openai_api_key:
    raise RuntimeError("openai_api_key is missing from secrets.yaml")

client_oa = OpenAI(api_key=openai_api_key)

github_token = secrets.get("github_token") or os.getenv("GITHUB_TOKEN", "")
client_github = (
    OpenAI(
        base_url="https://models.github.ai/inference",
        api_key=github_token,
    )
    if github_token
    else None
)
springer_api_key = secrets.get("springer_api_key") or os.getenv("SPRINGER_API_KEY", "")
