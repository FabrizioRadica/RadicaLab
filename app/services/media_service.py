"""Safe media/file handling: uploads, previews, and media path resolution."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from app.config import logger, settings
from app.services import project_service
from app.services.project_service import ProjectError

SAFE_KINDS = ("source", "outputs", "previews", "workflows", "metadata", "audio")


class MediaError(Exception):
    """Raised for upload/media failures with a user-readable message."""


def validate_image_upload(filename: str, data: bytes) -> str:
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext not in settings.allowed_image_extensions:
        allowed = ", ".join(settings.allowed_image_extensions)
        raise MediaError(f"File type '.{ext}' is not allowed. Allowed image types: {allowed}.")
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    if len(data) > max_bytes:
        raise MediaError(f"File is too large ({len(data) / 1024 / 1024:.1f} MB). Limit is {settings.max_upload_size_mb} MB.")
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
    except Exception as exc:
        raise MediaError("The uploaded file is not a valid image.") from exc
    return ext


def save_source_image(project_id: str, filename: str, data: bytes) -> dict:
    project = project_service.load_project(project_id)
    ext = validate_image_upload(filename, data)
    pdir = project_service.project_dir(project.folder)
    target_name = f"source_image.{ext}"
    source_dir = pdir / "source"
    source_dir.mkdir(exist_ok=True)
    for old in source_dir.glob("source_image.*"):
        old.unlink(missing_ok=True)
    (source_dir / target_name).write_bytes(data)
    project.source_image = target_name
    project_service.save_project(project)
    logger.info("Source image saved for project '%s': %s (%.1f KB)", project.name, target_name, len(data) / 1024)
    return {"filename": target_name, "url": f"/media/projects/{project.id}/source/{target_name}"}


def resolve_media_path(project_id: str, kind: str, filename: str) -> Path:
    """Resolve a media file inside a project subfolder, refusing traversal."""
    if kind not in SAFE_KINDS:
        raise MediaError(f"Invalid media kind '{kind}'.")
    safe_name = Path(filename).name
    if safe_name != filename or not safe_name:
        raise MediaError("Invalid file name.")
    project = project_service.load_project(project_id)
    base = (project_service.project_dir(project.folder) / kind).resolve()
    path = (base / safe_name).resolve()
    if base not in path.parents:
        raise MediaError("Invalid media path.")
    if not path.exists() or not path.is_file():
        raise ProjectError(f"File '{safe_name}' was not found in the project {kind} folder.")
    return path


MEDIA_CONTENT_TYPES = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".webp": "image/webp",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".json": "application/json",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
}


def content_type_for(path: Path) -> str:
    return MEDIA_CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
