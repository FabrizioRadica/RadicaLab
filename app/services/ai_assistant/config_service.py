"""AI Prompt Assistant configuration persistence (patch §12).

The existing app settings come from a read-only `.env` (see app/config.py) and
there is no writable app-level settings store, so the assistant persists its own
configuration to a single JSON file next to the project (BASE_DIR). Keys are
stored in that same file — the same way other local, single-user settings are
handled — and the UI documents this limitation (patch §12). Keys are NEVER sent
back to the browser (`AIAssistantConfig.safe_dict`).
"""

from __future__ import annotations

import json
import threading

from app.config import BASE_DIR, logger
from app.models.ai_assistant_models import AIAssistantConfig

CONFIG_FILE = BASE_DIR / "ai_assistant_config.json"

_LOCK = threading.RLock()
_cache: AIAssistantConfig | None = None


def load_config(force: bool = False) -> AIAssistantConfig:
    """Load (and cache) the assistant configuration. Missing/corrupt files fall
    back to defaults so the app always starts."""
    global _cache
    with _LOCK:
        if _cache is not None and not force:
            return _cache
        if not CONFIG_FILE.exists():
            _cache = AIAssistantConfig()
            return _cache
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            _cache = AIAssistantConfig.model_validate(data)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            logger.warning("AI assistant config unreadable (%s) — using defaults.", exc)
            _cache = AIAssistantConfig()
        return _cache


def save_config(config: AIAssistantConfig) -> AIAssistantConfig:
    global _cache
    payload = json.dumps(config.to_json_dict(), indent=2, ensure_ascii=False)
    with _LOCK:
        tmp = CONFIG_FILE.with_suffix(".json.tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(CONFIG_FILE)
        _cache = config
    logger.info("AI assistant config saved (provider=%s, enabled=%s).",
                config.provider.provider.value, config.prompt_assistant.enabled)
    return config


def update_config(changes: dict) -> AIAssistantConfig:
    """Merge a partial update (grouped by section) into the stored config.

    An empty/absent `provider.api_key` PRESERVES the existing key so the redacted
    UI round-trip never wipes it; a non-empty value replaces it."""
    current = load_config()
    data = current.to_json_dict()

    for section in ("prompt_assistant", "provider", "resources", "audio_feedback"):
        incoming = changes.get(section)
        if isinstance(incoming, dict):
            merged = dict(data.get(section, {}))
            merged.update(incoming)
            data[section] = merged

    # Preserve the stored API key unless the caller sent a real new one.
    incoming_provider = changes.get("provider") or {}
    if "api_key" in incoming_provider:
        new_key = (incoming_provider.get("api_key") or "").strip()
        if not new_key:
            data["provider"]["api_key"] = current.provider.api_key
    else:
        data["provider"]["api_key"] = current.provider.api_key
    # Never persist the redaction helper flag.
    data["provider"].pop("api_key_set", None)

    try:
        updated = AIAssistantConfig.model_validate(data)
    except ValueError as exc:
        raise ValueError(f"Invalid AI assistant settings: {exc}") from exc
    return save_config(updated)
