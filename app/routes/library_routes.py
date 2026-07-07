"""Video library page, video listing API and safe project media serving."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

from app.routes.shared import page_context, templates
from app.services import media_service, project_service
from app.services.media_service import MediaError
from app.services.project_service import ProjectError

router = APIRouter()


def _collect_videos() -> list[dict]:
    videos: list[dict] = []
    for summary in project_service.list_projects():
        try:
            project = project_service.load_project(summary.id)
        except ProjectError:
            continue
        for video in project.generated_videos:
            videos.append(
                {
                    "project_id": project.id,
                    "project_name": project.name,
                    "filename": video.filename,
                    "url": f"/media/projects/{project.id}/outputs/{video.filename}",
                    "preview": f"/media/projects/{project.id}/previews/{video.preview}" if video.preview else None,
                    "metadata_url": f"/media/projects/{project.id}/metadata/{video.metadata_file}" if video.metadata_file else None,
                    "mode": video.mode,
                    "model_id": video.model_id,
                    "resolution": video.resolution,
                    "fps": video.fps,
                    "frames": video.frames,
                    "seed": video.seed,
                    "is_mock": video.is_mock,
                    "has_audio": video.has_audio,
                    "has_effects": video.has_effects,
                    "raw_filename": video.raw_filename,
                    "duration": round(video.frames / video.fps, 1) if video.fps else None,
                    "created_at": video.created_at,
                }
            )
    videos.sort(key=lambda v: v["created_at"], reverse=True)
    return videos


@router.get("/library", response_class=HTMLResponse)
async def library_page(request: Request):
    return templates.TemplateResponse(request, "library.html", page_context(request, "library", videos=_collect_videos()))


@router.get("/api/videos")
async def api_videos():
    return _collect_videos()


@router.delete("/api/projects/{project_id}/videos")
async def api_delete_video(project_id: str, payload: dict):
    """Delete one generated video (plus its own preview/metadata files).

    The filename is validated against the project's generated-videos list and
    resolved from the known project directory — absolute paths and traversal
    are rejected. Model files, source images and .wanproj are never touched.
    """
    filename = str(payload.get("filename", ""))
    try:
        result = project_service.delete_generated_video(project_id, filename)
    except ProjectError as exc:
        status = 404 if "not found" in str(exc).lower() else 400
        raise HTTPException(status_code=status, detail=str(exc))
    return {"ok": True, "message": "Video deleted successfully.", **result}


@router.get("/media/projects/{project_id}/{kind}/{filename}")
async def serve_project_media(project_id: str, kind: str, filename: str, download: bool = False):
    try:
        path = media_service.resolve_media_path(project_id, kind, filename)
    except MediaError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ProjectError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'} if download else None
    return FileResponse(path, media_type=media_service.content_type_for(path), headers=headers)
