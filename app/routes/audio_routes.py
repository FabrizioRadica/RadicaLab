"""Audio Tracks API: upload, list, edit, remove tracks and apply them to a video."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, UploadFile

from app.config import settings
from app.services import audio_service, project_service
from app.services.audio_service import AudioError
from app.services.project_service import ProjectError

router = APIRouter()


def _load_project_or_404(project_id: str):
    try:
        return project_service.load_project(project_id)
    except ProjectError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


def _require_enabled() -> None:
    if not settings.audio_tracks_enabled:
        raise HTTPException(status_code=403, detail="Audio tracks are disabled (AUDIO_TRACKS_ENABLED=false).")


@router.get("/api/projects/{project_id}/audio")
async def api_list_audio(project_id: str):
    project = _load_project_or_404(project_id)
    return [t.model_dump(mode="json") for t in project.audio_tracks]


@router.post("/api/projects/{project_id}/audio/upload")
async def api_upload_audio(project_id: str, file: UploadFile):
    _require_enabled()
    _load_project_or_404(project_id)
    data = await file.read()
    try:
        audio_service.add_track(project_id, file.filename or "track", data)
    except AudioError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return await api_list_audio(project_id)


@router.patch("/api/projects/{project_id}/audio/{track_id}")
async def api_update_audio(project_id: str, track_id: str, payload: dict):
    _require_enabled()
    _load_project_or_404(project_id)
    try:
        track = audio_service.update_track(project_id, track_id, payload or {})
    except AudioError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return track.model_dump(mode="json")


@router.delete("/api/projects/{project_id}/audio/{track_id}")
async def api_delete_audio(project_id: str, track_id: str):
    _require_enabled()
    _load_project_or_404(project_id)
    try:
        audio_service.remove_track(project_id, track_id)
    except AudioError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return await api_list_audio(project_id)


@router.post("/api/projects/{project_id}/audio/apply")
async def api_apply_audio(project_id: str, payload: dict):
    _require_enabled()
    project = _load_project_or_404(project_id)
    video = str(payload.get("video_filename", "")).strip()
    if not video:
        # Default: the most recent raw (non-final) generated video.
        raw = [v for v in project.generated_videos if not v.has_audio]
        if not raw:
            raise HTTPException(status_code=400, detail="This project has no raw generated video yet.")
        video = raw[-1].filename
    try:
        entry = audio_service.apply_audio(project_id, video)
    except AudioError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "ok": True,
        "entry": entry.model_dump(mode="json"),
        "url": f"/media/projects/{project_id}/outputs/{entry.filename}",
    }
