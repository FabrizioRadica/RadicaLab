"""Project lifecycle: create, load, save, list, duplicate, delete.

Projects are stored as JSON in <PROJECTS_ROOT>/<folder>/project.wanproj with an
organized folder layout (source/, outputs/, workflows/, metadata/, previews/).
"""

from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path

from app.config import logger, settings
from app.models.generation_models import GenerationMode, GenerationParams, Orientation, Resolution
from app.models.project_models import Project, ProjectSummary

PROJECT_FILE = "project.wanproj"
SUBFOLDERS = ("source", "outputs", "workflows", "metadata", "previews")

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9 _\-]+")


class ProjectError(Exception):
    """Raised for project-level failures with a user-readable message."""


def sanitize_folder_name(name: str) -> str:
    cleaned = _SAFE_NAME_RE.sub("", name).strip()
    cleaned = re.sub(r"\s+", "_", cleaned)
    cleaned = cleaned.strip("._")
    if not cleaned:
        raise ProjectError("Project name must contain at least one letter or number.")
    return cleaned[:80]


def project_dir(folder: str) -> Path:
    """Resolve a project folder inside PROJECTS_ROOT, refusing path traversal."""
    root = settings.projects_root.resolve()
    path = (root / folder).resolve()
    if root not in path.parents and path != root:
        raise ProjectError("Invalid project folder path.")
    if path == root:
        raise ProjectError("Invalid project folder name.")
    return path


def _project_file(folder: str) -> Path:
    return project_dir(folder) / PROJECT_FILE


def create_project(
    name: str,
    description: str = "",
    tags: list[str] | None = None,
    generation_mode: str = "text2video",
    orientation: str = "landscape",
    width: int | None = None,
    height: int | None = None,
    model_id: str = "",
) -> Project:
    folder = sanitize_folder_name(name)
    base_folder = folder
    counter = 1
    while _project_file(folder).exists():
        counter += 1
        folder = f"{base_folder}_{counter:02d}"

    dw, dh = settings.default_resolution
    resolution = Resolution(width=width or dw, height=height or dh)
    mode = GenerationMode(generation_mode)
    if not model_id:
        model_id = settings.default_t2v_model if mode == GenerationMode.TEXT2VIDEO else settings.default_i2v_model

    params = GenerationParams(
        fps=settings.default_fps,
        frames=settings.default_frames,
        seed=settings.default_seed,
        random_seed=settings.default_seed < 0,
        control_after_generate=settings.default_control_after_generate,
        guidance_scale=settings.default_guidance_scale,
        sampler_name=settings.default_sampler_name,
        scheduler=settings.default_scheduler,
        denoise=settings.default_denoise,
        motion_strength=settings.default_motion_strength,
        output_format=settings.default_output_format,
    )
    params.advanced.steps = settings.default_steps
    params.advanced.device = "cuda" if settings.use_cuda else "cpu"
    params.advanced.memory_optimization = settings.enable_memory_optimization
    # ModelSamplingSD3 default for new projects (patch7 §4). Existing projects
    # keep their saved value and are never silently changed.
    params.model_sampling.enabled = settings.wan_model_sampling_sd3_enabled_default
    params.model_sampling.shift = settings.wan_model_sampling_sd3_shift_default

    # Apply the default Wan2.2 5B preset to NEW projects (patch8 §25 WAN_DEFAULT_PRESET)
    # so they start with practical, valid Wan 5B settings. Existing projects are
    # never touched. Never fails project creation if the preset can't be computed.
    try:
        from app.services import preset_service

        target = preset_service.compute_preset(
            settings.wan_default_preset, mode.value, orientation)
        if target is not None:
            # Explicit width/height passed by the caller still win over the preset.
            if width is None and height is None:
                resolution = Resolution(width=target["resolution"]["width"],
                                        height=target["resolution"]["height"])
            params.frames = target["frames"]
            params.fps = target["fps"]
            params.advanced.steps = target["steps"]
            params.sampler_name = target["sampler_name"]
            params.scheduler = target["scheduler"]
            params.guidance_scale = target["guidance_scale"]
            params.denoise = target["denoise"]
            params.model_sampling.enabled = target["model_sampling"]["enabled"]
            params.model_sampling.shift = target["model_sampling"]["shift"]
            params.advanced.offload_policy = target["offload_policy"]
            params.advanced.unload_model_after_generation = target["unload_model_after_generation"]
            params.wan_preset = target["wan_preset"]
    except Exception as exc:  # noqa: BLE001 — preset default must not break creation
        logger.warning("Could not apply default Wan preset to new project: %s", exc)

    project = Project(
        id=uuid.uuid4().hex[:12],
        name=name.strip(),
        folder=folder,
        description=description.strip(),
        tags=[t.strip() for t in (tags or []) if t.strip()],
        generation_mode=mode,
        orientation=Orientation(orientation),
        resolution=resolution,
        model_id=model_id,
        params=params,
        app_version=settings.app_version,
    )

    pdir = project_dir(folder)
    pdir.mkdir(parents=True, exist_ok=True)
    for sub in SUBFOLDERS:
        (pdir / sub).mkdir(exist_ok=True)
    save_project(project)
    logger.info("Project created: '%s' (%s) in %s", project.name, project.id, pdir)
    return project


def save_project(project: Project) -> Project:
    from app.models.project_models import utc_now

    project.updated_at = utc_now()
    pfile = _project_file(project.folder)
    pfile.parent.mkdir(parents=True, exist_ok=True)
    tmp = pfile.with_suffix(".wanproj.tmp")
    tmp.write_text(json.dumps(project.to_wanproj_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(pfile)
    logger.info("Project saved: '%s' (%s)", project.name, project.id)
    return project


def load_project_by_folder(folder: str) -> Project:
    pfile = _project_file(folder)
    if not pfile.exists():
        raise ProjectError(f"Project file not found in folder '{folder}'.")
    try:
        data = json.loads(pfile.read_text(encoding="utf-8"))
        return Project.model_validate(data)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.error("Corrupted project file %s: %s", pfile, exc)
        raise ProjectError(f"Project file for '{folder}' is corrupted or has an invalid format.") from exc


def _iter_project_files():
    if not settings.projects_root.exists():
        return
    for child in sorted(settings.projects_root.iterdir()):
        pfile = child / PROJECT_FILE
        if child.is_dir() and pfile.exists():
            yield child, pfile


def load_project(project_id: str) -> Project:
    for child, _ in _iter_project_files() or []:
        try:
            project = load_project_by_folder(child.name)
        except ProjectError:
            continue
        if project.id == project_id:
            return project
    raise ProjectError(f"Project '{project_id}' not found.")


def list_projects() -> list[ProjectSummary]:
    summaries: list[ProjectSummary] = []
    for child, _ in _iter_project_files() or []:
        try:
            p = load_project_by_folder(child.name)
        except ProjectError:
            logger.warning("Skipping unreadable project folder: %s", child)
            continue
        thumbnail = None
        for video in reversed(p.generated_videos):
            if video.preview:
                thumbnail = f"/media/projects/{p.id}/previews/{video.preview}"
                break
        summaries.append(
            ProjectSummary(
                id=p.id,
                name=p.name,
                folder=p.folder,
                description=p.description,
                tags=p.tags,
                generation_mode=p.generation_mode.value,
                orientation=p.orientation.value,
                resolution=p.resolution.label(),
                fps=p.params.fps,
                frames=p.params.frames,
                model_id=p.model_id,
                video_count=len(p.generated_videos),
                thumbnail=thumbnail,
                created_at=p.created_at,
                updated_at=p.updated_at,
            )
        )
    summaries.sort(key=lambda s: s.updated_at, reverse=True)
    return summaries


def delete_project(project_id: str) -> str:
    project = load_project(project_id)
    pdir = project_dir(project.folder)
    shutil.rmtree(pdir)
    logger.info("Project deleted: '%s' (%s)", project.name, project.id)
    return project.name


def duplicate_project(project_id: str) -> Project:
    source = load_project(project_id)
    copy_name = f"{source.name} Copy"
    folder = sanitize_folder_name(copy_name)
    base_folder = folder
    counter = 1
    while _project_file(folder).exists():
        counter += 1
        folder = f"{base_folder}_{counter:02d}"

    duplicate = source.model_copy(deep=True)
    duplicate.id = uuid.uuid4().hex[:12]
    duplicate.name = copy_name
    duplicate.folder = folder
    duplicate.generated_videos = []
    duplicate.exported_workflows = []
    from app.models.project_models import utc_now

    duplicate.created_at = utc_now()

    src_dir = project_dir(source.folder)
    dst_dir = project_dir(folder)
    dst_dir.mkdir(parents=True, exist_ok=True)
    for sub in SUBFOLDERS:
        (dst_dir / sub).mkdir(exist_ok=True)
    src_assets = src_dir / "source"
    if src_assets.exists():
        for item in src_assets.iterdir():
            if item.is_file():
                shutil.copy2(item, dst_dir / "source" / item.name)
    save_project(duplicate)
    logger.info("Project duplicated: '%s' -> '%s'", source.name, duplicate.name)
    return duplicate


# Generated-output folders a video delete is allowed to touch. Model files,
# source/reference images, audio, workflows and the .wanproj file are NEVER
# deletable through this path.
GENERATED_OUTPUT_KINDS = ("outputs", "previews", "metadata")


def _safe_generated_filename(filename: str) -> str:
    """Validate a client-supplied generated-file name against path traversal.

    Rejects empty names, `..`, absolute/drive paths and any separator — the
    final path is always resolved from the known project directory.
    """
    name = (filename or "").strip()
    if (not name or name != Path(name).name or ".." in name
            or "/" in name or "\\" in name or ":" in name
            or name.startswith(("~", ".")) or name == PROJECT_FILE):
        raise ProjectError("Invalid video file name.")
    return name


def _delete_generated_file(pdir: Path, kind: str, filename: str) -> bool:
    """Delete one file strictly inside a generated-output subfolder."""
    if kind not in GENERATED_OUTPUT_KINDS:
        raise ProjectError(f"Deleting files of kind '{kind}' is not allowed.")
    name = _safe_generated_filename(filename)
    base = (pdir / kind).resolve()
    path = (base / name).resolve()
    if base not in path.parents:
        raise ProjectError("Invalid video file path.")
    if path.exists() and path.is_file():
        path.unlink()
        return True
    return False


def delete_generated_video(project_id: str, filename: str) -> dict:
    """Delete one generated video plus its own preview/metadata files.

    Only files registered in the project's generated_videos list can be
    deleted, and only inside outputs/, previews/ and metadata/. Raw/final
    related videos are never deleted automatically.
    """
    name = _safe_generated_filename(filename)
    project = load_project(project_id)
    entry = next((v for v in project.generated_videos if v.filename == name), None)
    if entry is None:
        raise ProjectError(f"Video '{name}' is not a generated video of this project.")
    pdir = project_dir(project.folder)
    others = [v for v in project.generated_videos if v.filename != name]

    deleted: list[str] = []
    if _delete_generated_file(pdir, "outputs", entry.filename):
        deleted.append(f"outputs/{entry.filename}")
    # Preview/metadata are deleted only when no other entry references them.
    if entry.preview and not any(v.preview == entry.preview for v in others):
        if _delete_generated_file(pdir, "previews", entry.preview):
            deleted.append(f"previews/{entry.preview}")
    if entry.metadata_file and not any(v.metadata_file == entry.metadata_file for v in others):
        if _delete_generated_file(pdir, "metadata", entry.metadata_file):
            deleted.append(f"metadata/{entry.metadata_file}")

    project.generated_videos = others
    save_project(project)
    logger.info("Generated video deleted from project '%s': %s (%s)",
                project.name, name, ", ".join(deleted) or "no files on disk")
    return {"filename": name, "deleted_files": deleted}


def next_output_index(project: Project) -> int:
    outputs = project_dir(project.folder) / "outputs"
    max_index = 0
    if outputs.exists():
        for f in outputs.iterdir():
            match = re.search(r"_(\d{4})\.\w+$", f.name)
            if match:
                max_index = max(max_index, int(match.group(1)))
    return max(max_index, len(project.generated_videos)) + 1
