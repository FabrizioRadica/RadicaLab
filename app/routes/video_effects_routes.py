"""Video Effects / Color & Look API: settings and post-processing apply."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.services import project_service, video_effects_service
from app.services.project_service import ProjectError
from app.services.video_effects_service import VideoEffectsError

router = APIRouter()


def _check(project_id: str) -> None:
    if not settings.video_effects_enabled:
        raise HTTPException(status_code=403,
                            detail="Video effects are disabled (VIDEO_EFFECTS_ENABLED=false).")
    try:
        project_service.load_project(project_id)
    except ProjectError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/api/projects/{project_id}/video-effects")
async def api_get_video_effects(project_id: str):
    _check(project_id)
    return video_effects_service.get_effects(project_id).model_dump(mode="json")


@router.patch("/api/projects/{project_id}/video-effects")
async def api_update_video_effects(project_id: str, payload: dict):
    _check(project_id)
    try:
        fx = video_effects_service.update_effects(project_id, payload or {})
    except VideoEffectsError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return fx.model_dump(mode="json")


@router.post("/api/projects/{project_id}/video-effects/apply")
async def api_apply_video_effects(project_id: str, payload: dict):
    _check(project_id)
    video = str((payload or {}).get("video_filename", "")).strip()
    if not video:
        raise HTTPException(status_code=400, detail="Missing 'video_filename'.")
    try:
        entry = video_effects_service.apply_effects(project_id, video)
    except VideoEffectsError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "ok": True,
        "entry": entry.model_dump(mode="json"),
        "url": f"/media/projects/{project_id}/outputs/{entry.filename}",
    }
