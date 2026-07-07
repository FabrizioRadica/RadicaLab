"""Home (project editor) and About pages."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.config import settings
from app.routes.shared import page_context, templates
from app.services import camera_motion_service, model_service, project_service

router = APIRouter()


def _editor_context(request: Request, project_id: str | None):
    projects = project_service.list_projects()
    current = None
    if project_id:
        try:
            current = project_service.load_project(project_id)
        except project_service.ProjectError:
            current = None
    if current is None and projects:
        try:
            current = project_service.load_project(projects[0].id)
        except project_service.ProjectError:
            current = None
    try:
        models = model_service.list_models()
    except model_service.ModelError:
        models = []
    return page_context(
        request,
        "home",
        projects=projects,
        current_project=current,
        current_project_json=current.to_wanproj_dict() if current else None,
        models=[m.model_dump(mode="json") for m in models],
        camera_options=camera_motion_service.get_options(),
        defaults={
            "resolution": f"{settings.default_resolution[0]}x{settings.default_resolution[1]}",
            "test_presets": {
                "test_low_landscape": settings.test_low_landscape,
                "test_low_portrait": settings.test_low_portrait,
                "test_quick_landscape": settings.test_quick_landscape,
                "test_quick_portrait": settings.test_quick_portrait,
            },
        },
    )


@router.get("/", response_class=HTMLResponse)
async def home(request: Request, project: str | None = None):
    return templates.TemplateResponse(request, "home.html", _editor_context(request, project))


@router.get("/about", response_class=HTMLResponse)
async def about(request: Request):
    return templates.TemplateResponse(request, "about.html", page_context(request, "about"))
