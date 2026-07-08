"""AIResourceManagerModule (patch §5, §14).

Keeps local LLM inference from stealing VRAM/RAM that WAN needs:

- knows whether a WAN/video render (single-clip job OR sequence queue) is running
  by querying the existing job/queue managers — no new isolated state (patch §11);
- blocks local LLM generation during a render by default (§5.2), overridable;
- serializes prompt generations (no parallel local inference, §5.1);
- releases local LLM resources after generation whenever technically possible
  and reports honest status (§5.3, §14).
"""

from __future__ import annotations

import asyncio
import time

from app.config import logger
from app.models.ai_assistant_models import AIResourceSettings
from app.services.ai_assistant.providers import AIProviderBase, AIProviderError

# UI status strings (patch §5.3).
STATUS_IDLE = "AI Assistant idle"
STATUS_GENERATING_PROMPT = "AI Assistant generating prompt..."
STATUS_GENERATING_SEQUENCE = "AI Assistant generating sequence plan..."
STATUS_APPLYING = "AI Assistant applying prompts..."
STATUS_ADDING_CLIPS = "AI Assistant adding clips to SequenceQueue..."
STATUS_RELEASING = "AI Assistant releasing model resources..."
STATUS_RELEASED = "AI resources released"
STATUS_NO_UNLOAD = "Provider does not support explicit unload; local server may keep the model in memory"
STATUS_BLOCKED = "WAN render is running; local AI generation is blocked by settings"


class AIResourceManager:
    def __init__(self) -> None:
        self._status = STATUS_IDLE
        self._busy = False
        self._lock = asyncio.Lock()
        self.last_release_status = None

    # -- render awareness ---------------------------------------------------
    def is_render_running(self) -> bool:
        """True if a single-clip generation job OR a sequence render is active."""
        try:
            from app.services.generation_service import job_manager
            active = any(j.status.value in ("pending", "running", "cancel_requested")
                         for j in job_manager.list())
            if active:
                return True
        except Exception as exc:  # noqa: BLE001 — never break the assistant
            logger.debug("Could not query generation jobs: %s", exc)
        try:
            from app.services.sequence_queue_service import queue_manager
            return queue_manager.any_running()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not query sequence queue: %s", exc)
            return False

    @property
    def status(self) -> str:
        return self._status

    def set_status(self, status: str) -> None:
        self._status = status

    # -- memory checks ------------------------------------------------------
    def _free_memory_gb(self) -> tuple[float | None, float | None]:
        """(free_vram_gb, free_ram_gb) using the existing system monitor. Either
        may be None when unavailable."""
        free_vram = free_ram = None
        try:
            from app.services import system_monitor_service
            stats = system_monitor_service.get_system_stats()
            vram = stats.get("vram") or {}
            if vram.get("available") and vram.get("total_gb") is not None:
                free_vram = round(float(vram["total_gb"]) - float(vram.get("used_gb", 0)), 2)
            ram = stats.get("ram") or {}
            if ram.get("available") and ram.get("total_gb") is not None:
                free_ram = round(float(ram["total_gb"]) - float(ram.get("used_gb", 0)), 2)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Free-memory probe failed: %s", exc)
        return free_vram, free_ram

    def preflight_warnings(self, settings: AIResourceSettings, is_local: bool) -> list[str]:
        """Non-blocking advisories about VRAM/RAM headroom (patch §3.3)."""
        warnings: list[str] = []
        if not is_local:
            return warnings
        free_vram, free_ram = self._free_memory_gb()
        if settings.min_free_vram_gb and free_vram is not None and free_vram < settings.min_free_vram_gb:
            warnings.append(
                f"Low free VRAM ({free_vram:.1f} GB < {settings.min_free_vram_gb:.1f} GB). "
                "Local AI generation may fail or slow WAN afterwards.")
        elif settings.warn_high_vram and free_vram is not None and free_vram < 1.5:
            warnings.append(f"Free VRAM is very low ({free_vram:.1f} GB).")
        if settings.min_free_ram_gb and free_ram is not None and free_ram < settings.min_free_ram_gb:
            warnings.append(
                f"Low free RAM ({free_ram:.1f} GB < {settings.min_free_ram_gb:.1f} GB).")
        elif settings.warn_high_ram and free_ram is not None and free_ram < 1.5:
            warnings.append(f"Free RAM is very low ({free_ram:.1f} GB).")
        return warnings

    # -- guarded generation -------------------------------------------------
    def acquire(self, settings: AIResourceSettings, is_local: bool) -> asyncio.Lock:
        """Return the serialization lock after enforcing the render-conflict and
        parallel-generation rules. Raises AIProviderError when blocked."""
        if is_local and settings.block_during_render and not settings.allow_during_render \
                and self.is_render_running():
            self.set_status(STATUS_BLOCKED)
            raise AIProviderError(
                "WAN rendering is currently running. Local AI generation is blocked by "
                "settings. Wait for the render to finish, or enable 'Allow local AI "
                "assistant during render'.")
        if self._busy:
            raise AIProviderError("The AI Assistant is already generating. Please wait for it to finish.")
        return self._lock

    async def release(self, provider: AIProviderBase | None = None,
                      settings: AIResourceSettings | None = None,
                      is_local: bool = False) -> dict:
        """Release local LLM resources after generation where possible, then run
        the app's conservative VRAM cleanup so WAN has headroom (patch §14).

        This method is a SEPARATE phase from prompt generation (patch §5/§10):
        it is safe to call with no arguments, it NEVER raises, and any cleanup
        failure is returned as a non-blocking warning — a successful prompt is
        never discarded because cleanup failed. Every result carries `ok` and
        `warning` keys following the patch convention."""
        try:
            # Resolve provider/settings from the saved config when the caller did
            # not supply them, so `release()` is safe to call bare (patch §5).
            if provider is None or settings is None:
                from app.services.ai_assistant import config_service
                from app.services.ai_assistant.providers import get_provider
                cfg = config_service.load_config()
                if settings is None:
                    settings = cfg.resources
                if provider is None:
                    provider = get_provider(cfg.provider)
                    is_local = cfg.provider.is_local()

            supports = provider.supports_explicit_unload() if provider is not None else False

            # Cloud providers / release disabled → lightweight no-op (patch §7).
            if not is_local or (settings is not None and not settings.release_after_generation):
                self.set_status(STATUS_IDLE)
                self.last_release_status = "remote_resources_released" if not is_local \
                    else "local_release_disabled"
                return {"ok": True, "released": False, "supports_unload": supports,
                        "warning": None, "message": "No local resources to release.",
                        "reason": "no_local_release", "status": STATUS_IDLE}

            # Local provider (Ollama / LM Studio): attempt a real, provider-safe
            # release. providers.*.release_resources() already catch their own
            # errors and never destroy the user's external server (patch §6).
            self.set_status(STATUS_RELEASING)
            result = await provider.release_resources()

            # Conservative local cleanup (never kills processes / deletes files).
            try:
                from app.services import gpu_memory_service
                gpu_memory_service.cleanup_vram(reason="ai_assistant_release", unload_models=False)
            except Exception as exc:  # noqa: BLE001
                logger.debug("VRAM cleanup after AI release failed: %s", exc)

            cooldown = max(getattr(settings, "cooldown_after_release_seconds", 0.0) or 0.0, 0.0)
            if cooldown:
                await asyncio.sleep(min(cooldown, 10.0))

            released = bool(result.get("released"))
            result.setdefault("ok", True)
            result.setdefault("warning", None)
            result["status"] = STATUS_RELEASED if released else STATUS_NO_UNLOAD
            self.last_release_status = "local_resources_released" if released else result.get("message")
            self.set_status(result["status"])
            return result

        except Exception as exc:  # noqa: BLE001 — cleanup must NEVER break generation
            warning = f"AI resource cleanup failed: {exc}"
            self.last_release_status = warning
            logger.warning("[AIResourceManager] %s", warning)
            self.set_status(STATUS_NO_UNLOAD)
            return {"ok": False, "released": False, "supports_unload": False,
                    "warning": warning, "message": warning,
                    "reason": "cleanup_exception", "status": STATUS_NO_UNLOAD}

    def snapshot(self) -> dict:
        free_vram, free_ram = self._free_memory_gb()
        return {
            "status": self._status,
            "busy": self._busy,
            "render_running": self.is_render_running(),
            "free_vram_gb": free_vram,
            "free_ram_gb": free_ram,
        }


resource_manager = AIResourceManager()
