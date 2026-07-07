"""Project pages and REST API."""

from __future__ import annotations

import os
import sys

from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse

from app.config import logger, settings
from app.models.project_models import Project
from app.routes.shared import page_context, templates
from app.services import media_service, project_service
from app.services.media_service import MediaError
from app.services.project_service import ProjectError

router = APIRouter()


@router.get("/projects", response_class=HTMLResponse)
async def projects_page(request: Request):
    return templates.TemplateResponse(request, "projects.html", page_context(request, "projects", projects=project_service.list_projects())
    )


@router.get("/api/projects")
async def api_list_projects():
    return [s.model_dump() for s in project_service.list_projects()]


@router.post("/api/projects")
async def api_create_project(payload: dict):
    try:
        project = project_service.create_project(
            name=str(payload.get("name", "")).strip(),
            description=str(payload.get("description", "")),
            tags=payload.get("tags") or [],
            generation_mode=payload.get("generation_mode", settings.default_generation_mode),
            orientation=payload.get("orientation", settings.default_orientation),
            width=payload.get("width"),
            height=payload.get("height"),
            model_id=payload.get("model_id", ""),
        )
    except (ProjectError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return project.to_wanproj_dict()


@router.get("/api/projects/{project_id}")
async def api_get_project(project_id: str):
    try:
        return project_service.load_project(project_id).to_wanproj_dict()
    except ProjectError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.put("/api/projects/{project_id}")
async def api_save_project(project_id: str, payload: dict):
    try:
        existing = project_service.load_project(project_id)
    except ProjectError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    try:
        # Immutable/server-managed fields cannot be overwritten by the client.
        payload = dict(payload)
        for locked in ("id", "folder", "created_at", "generated_videos", "exported_workflows",
                       "credits", "schema", "app_version", "source_image", "audio_tracks",
                       "video_effects"):
            payload.pop(locked, None)
        merged = existing.to_wanproj_dict()
        merged.update(payload)
        project = Project.model_validate(merged)
        project_service.save_project(project)
        return project.to_wanproj_dict()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid project data: {exc}")


@router.delete("/api/projects/{project_id}")
async def api_delete_project(project_id: str):
    try:
        name = project_service.delete_project(project_id)
    except ProjectError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"ok": True, "message": f"Project '{name}' deleted."}


@router.post("/api/projects/{project_id}/duplicate")
async def api_duplicate_project(project_id: str):
    try:
        duplicate = project_service.duplicate_project(project_id)
    except ProjectError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return duplicate.to_wanproj_dict()


@router.post("/api/projects/{project_id}/open-folder")
async def api_open_folder(project_id: str):
    """Open the project folder in the OS file explorer (local tool convenience)."""
    try:
        project = project_service.load_project(project_id)
        pdir = project_service.project_dir(project.folder)
    except ProjectError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    try:
        if sys.platform == "win32":
            os.startfile(str(pdir))  # noqa: S606 — opening a validated project directory
        else:
            import subprocess

            opener = "open" if sys.platform == "darwin" else "xdg-open"
            subprocess.Popen([opener, str(pdir)])
        logger.info("Opened project folder: %s", pdir)
        return {"ok": True, "path": str(pdir)}
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not open folder: {exc}")


@router.post("/api/projects/{project_id}/source-image")
async def api_upload_source_image(project_id: str, file: UploadFile):
    data = await file.read()
    try:
        result = media_service.save_source_image(project_id, file.filename or "image", data)
    except MediaError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ProjectError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return result
