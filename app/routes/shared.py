"""Shared template environment and helpers for all routers."""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.config import settings

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals.update(
    app_name=settings.app_name,
    app_version=settings.app_version,
    credits_concept=settings.credits_concept,
    credits_project=settings.credits_project,
    comfyui_export_enabled=settings.comfyui_export_enabled,
    audio_tracks_enabled=settings.audio_tracks_enabled,
    video_effects_enabled=settings.video_effects_enabled,
    system_monitor_enabled=settings.system_monitor_enabled,
    system_monitor_poll_ms=settings.system_monitor_poll_interval_ms,
    system_monitor_show_gpu=settings.system_monitor_show_gpu,
    system_monitor_show_disk=settings.system_monitor_show_disk,
    gen_progress_poll_ms=settings.generation_progress_poll_interval_ms,
    gen_completed_visible_s=settings.generation_completed_visible_seconds,
    gen_failed_visible_s=settings.generation_failed_visible_seconds,
)


def page_context(request, active: str, **extra) -> dict:
    return {"request": request, "active": active, **extra}
