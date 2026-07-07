"""Settings pages and safe settings API."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.routes.shared import page_context, templates
from app.services import model_service, settings_service

router = APIRouter()


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    try:
        models = [m.model_dump(mode="json") for m in model_service.list_models()]
    except model_service.ModelError:
        models = []
    return templates.TemplateResponse(request, "settings.html", page_context(request, "settings", config=settings_service.safe_settings(), models=models),
    )


@router.get("/api/settings")
async def api_settings():
    return settings_service.safe_settings()


@router.get("/api/logs")
async def api_logs(lines: int = 200):
    return {"lines": settings_service.read_log_tail(min(max(lines, 10), 1000))}
