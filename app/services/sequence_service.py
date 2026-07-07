"""VideoSequenceQueue persistence + CRUD (patchRC2 §9/§12).

A sequence lives in its own folder under PROJECTS_ROOT with the layout required
by §12.3:

    projects/<folder>/
      sequence.json
      assets/images/            uploaded reference images (stable copies)
      assets/audio/             uploaded audio files (stable copies)
      source/                   staging dir the render backend reads I2V images from
      clips/clip_001/{raw,fx,final}/  metadata.json
      exports/{merged,final}/

Sequences use `sequence.json`, so `project_service.list_projects()` (which keys
on `project.wanproj`) never picks them up — single-clip behavior is untouched.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import uuid
from pathlib import Path

from app.config import logger, settings
from app.models.generation_models import GenerationMode, GenerationParams, Orientation, Resolution
from app.models.project_models import AudioTrack, Project
from app.models.sequence_models import (
    ClipGenerationOverrides,
    ClipType,
    GlobalGenerationSettings,
    OutputMode,
    SequenceClip,
    VideoSequence,
    VramMode,
    utc_now,
)
from app.services import project_service

SEQUENCE_FILE = "sequence.json"

# Serializes sequence.json reads and atomic writes so a background render
# thread's replace() never collides with a concurrent status-poll read
# (Windows raises PermissionError on read-during-replace).
_IO_LOCK = threading.RLock()


class SequenceError(Exception):
    """Raised for sequence failures with a user-readable message."""


# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

def sequence_dir(folder: str) -> Path:
    """Resolve a sequence folder inside PROJECTS_ROOT, refusing path traversal."""
    root = settings.projects_root.resolve()
    path = (root / folder).resolve()
    if root not in path.parents and path != root:
        raise SequenceError("Invalid sequence folder path.")
    if path == root:
        raise SequenceError("Invalid sequence folder name.")
    return path


def _sequence_file(folder: str) -> Path:
    return sequence_dir(folder) / SEQUENCE_FILE


def clip_dir(seq: VideoSequence, clip: SequenceClip) -> Path:
    return sequence_dir(seq.folder) / "clips" / f"clip_{clip.index + 1:03d}"


def assets_images_dir(seq: VideoSequence) -> Path:
    d = sequence_dir(seq.folder) / "assets" / "images"
    d.mkdir(parents=True, exist_ok=True)
    return d


def assets_audio_dir(seq: VideoSequence) -> Path:
    d = sequence_dir(seq.folder) / "assets" / "audio"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ensure_layout(folder: str) -> None:
    base = sequence_dir(folder)
    for sub in ("assets/images", "assets/audio", "source", "clips",
                "exports/merged", "exports/final"):
        (base / sub).mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------
# Load / save / list
# --------------------------------------------------------------------------

def save_sequence(seq: VideoSequence) -> VideoSequence:
    seq.updated_at = utc_now()
    seq.reindex()
    sfile = _sequence_file(seq.folder)
    sfile.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(seq.to_json_dict(), indent=2, ensure_ascii=False)
    with _IO_LOCK:
        tmp = sfile.with_suffix(".json.tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(sfile)
    return seq


def load_sequence_by_folder(folder: str) -> VideoSequence:
    sfile = _sequence_file(folder)
    with _IO_LOCK:
        if not sfile.exists():
            raise SequenceError(f"Sequence file not found in folder '{folder}'.")
        try:
            data = json.loads(sfile.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            logger.error("Corrupted sequence file %s: %s", sfile, exc)
            raise SequenceError(f"Sequence file for '{folder}' is corrupted.") from exc
    try:
        return VideoSequence.model_validate(data)
    except ValueError as exc:
        logger.error("Invalid sequence file %s: %s", sfile, exc)
        raise SequenceError(f"Sequence file for '{folder}' is corrupted.") from exc


def _iter_sequence_files():
    if not settings.projects_root.exists():
        return
    for child in sorted(settings.projects_root.iterdir()):
        sfile = child / SEQUENCE_FILE
        if child.is_dir() and sfile.exists():
            yield child, sfile


def load_sequence(sequence_id: str) -> VideoSequence:
    for child, _ in _iter_sequence_files() or []:
        try:
            seq = load_sequence_by_folder(child.name)
        except SequenceError:
            continue
        if seq.sequence_id == sequence_id:
            return seq
    raise SequenceError(f"Sequence '{sequence_id}' not found.")


def list_sequences() -> list[dict]:
    out: list[dict] = []
    for child, _ in _iter_sequence_files() or []:
        try:
            seq = load_sequence_by_folder(child.name)
        except SequenceError:
            logger.warning("Skipping unreadable sequence folder: %s", child)
            continue
        completed = sum(1 for c in seq.clips if c.status.value == "completed")
        thumb = None
        for clip in seq.clips:
            if clip.outputs.preview:
                thumb = f"/media/sequences/{seq.sequence_id}/clip/{clip.clip_id}/preview"
                break
        out.append({
            "sequence_id": seq.sequence_id,
            "name": seq.name,
            "folder": seq.folder,
            "clips_total": len(seq.clips),
            "clips_completed": completed,
            "status": seq.render_state.status.value,
            "output_mode": seq.output_mode.value,
            "thumbnail": thumb,
            "final_output": seq.outputs.final,
            "created_at": seq.created_at,
            "updated_at": seq.updated_at,
        })
    out.sort(key=lambda s: s["updated_at"], reverse=True)
    return out


# --------------------------------------------------------------------------
# Create / update / delete
# --------------------------------------------------------------------------

def _unique_folder(name: str) -> str:
    folder = project_service.sanitize_folder_name(name)
    base = folder
    counter = 1
    while (settings.projects_root / folder / SEQUENCE_FILE).exists() or \
            (settings.projects_root / folder / project_service.PROJECT_FILE).exists():
        counter += 1
        folder = f"{base}_{counter:02d}"
    return folder


def create_sequence(name: str, description: str = "", model_id: str = "") -> VideoSequence:
    folder = _unique_folder(name)
    gs = GlobalGenerationSettings()
    if not model_id:
        model_id = settings.default_t2v_model
    gs.model_id = model_id
    gs.wan_preset = settings.wan_default_preset
    gs.negative_prompt = ""
    # Seed the global settings from the app's Wan preset defaults when available.
    try:
        from app.services import preset_service

        target = preset_service.compute_preset(settings.wan_default_preset, "text2video",
                                                settings.default_orientation)
        if target:
            gs.width = target["resolution"]["width"]
            gs.height = target["resolution"]["height"]
            gs.frames = target["frames"]
            gs.fps = target["fps"]
            gs.steps = target["steps"]
            gs.guidance_scale = target["guidance_scale"]
            gs.sampler_name = target["sampler_name"]
            gs.scheduler = target["scheduler"]
            gs.denoise = target["denoise"]
            gs.model_sampling_enabled = target["model_sampling"]["enabled"]
            gs.model_sampling_shift = target["model_sampling"]["shift"]
            gs.wan_preset = target["wan_preset"]
    except Exception as exc:  # noqa: BLE001 — defaults must not break creation
        logger.warning("Could not seed sequence global settings from preset: %s", exc)

    try:
        output_mode = OutputMode(settings.sequence_default_output_mode)
    except ValueError:
        output_mode = OutputMode.CLIPS_ONLY
    try:
        vram_mode = VramMode(settings.sequence_default_vram_mode)
    except ValueError:
        vram_mode = VramMode.BALANCED

    seq = VideoSequence(
        sequence_id=uuid.uuid4().hex[:12],
        name=name.strip(),
        folder=folder,
        description=description.strip(),
        global_generation_settings=gs,
        output_mode=output_mode,
        vram_mode=vram_mode,
        continue_on_error=not settings.sequence_stop_on_clip_error,
        app_version=settings.app_version,
    )
    _ensure_layout(folder)
    save_sequence(seq)
    logger.info("Sequence created: '%s' (%s) in %s", seq.name, seq.sequence_id, folder)
    return seq


_SEQUENCE_TOP_FIELDS = ("name", "description", "output_mode", "vram_mode", "continue_on_error")


def update_sequence(sequence_id: str, changes: dict) -> VideoSequence:
    seq = load_sequence(sequence_id)
    data = seq.model_dump(by_alias=True)
    for key in _SEQUENCE_TOP_FIELDS:
        if key in changes:
            data[key] = changes[key]
    if "global_generation_settings" in changes and isinstance(changes["global_generation_settings"], dict):
        data["global_generation_settings"].update(changes["global_generation_settings"])
    if "global_color_look" in changes and isinstance(changes["global_color_look"], dict):
        # Deep-merge nested effect groups so partial updates work like the
        # single-clip video_effects update.
        cur = data.get("global_color_look", {})
        for k, v in changes["global_color_look"].items():
            if isinstance(v, dict) and isinstance(cur.get(k), dict):
                cur[k].update(v)
            else:
                cur[k] = v
        data["global_color_look"] = cur
    try:
        updated = VideoSequence.model_validate(data)
    except ValueError as exc:
        raise SequenceError(f"Invalid sequence settings: {exc}") from exc
    return save_sequence(updated)


def delete_sequence(sequence_id: str) -> str:
    seq = load_sequence(sequence_id)
    shutil.rmtree(sequence_dir(seq.folder))
    logger.info("Sequence deleted: '%s' (%s)", seq.name, seq.sequence_id)
    return seq.name


# --------------------------------------------------------------------------
# Clip CRUD
# --------------------------------------------------------------------------

def add_clip(sequence_id: str, data: dict) -> SequenceClip:
    seq = load_sequence(sequence_id)
    ctype = ClipType(data.get("type", "prompt_only"))
    clip = SequenceClip(
        clip_id=uuid.uuid4().hex[:10],
        index=len(seq.clips),
        name=(data.get("name") or f"Clip {len(seq.clips) + 1:02d}").strip(),
        type=ctype,
        prompt=(data.get("prompt") or "").strip(),
        negative_prompt=(data.get("negative_prompt") or "").strip(),
        source_image=data.get("source_image") or None,
        image_fit=data.get("image_fit", "contain"),
    )
    seq.clips.append(clip)
    save_sequence(seq)
    logger.info("Clip added to sequence '%s': %s (%s)", seq.name, clip.name, clip.type.value)
    return clip


_CLIP_TOP_FIELDS = ("name", "type", "prompt", "negative_prompt", "source_image",
                    "image_fit", "use_global_generation_settings", "color_look_mode")


def update_clip(sequence_id: str, clip_id: str, changes: dict) -> SequenceClip:
    seq = load_sequence(sequence_id)
    clip = seq.get_clip(clip_id)
    if clip is None:
        raise SequenceError(f"Clip '{clip_id}' not found.")
    data = clip.model_dump()
    for key in _CLIP_TOP_FIELDS:
        if key in changes and changes[key] is not None:
            data[key] = changes[key]
    if "generation_overrides" in changes and isinstance(changes["generation_overrides"], dict):
        data["generation_overrides"] = {**data.get("generation_overrides", {}),
                                        **changes["generation_overrides"]}
    if "custom_color_look" in changes and isinstance(changes["custom_color_look"], dict):
        cur = data.get("custom_color_look", {})
        for k, v in changes["custom_color_look"].items():
            if isinstance(v, dict) and isinstance(cur.get(k), dict):
                cur[k].update(v)
            else:
                cur[k] = v
        data["custom_color_look"] = cur
    data["updated_at"] = utc_now()
    try:
        updated = SequenceClip.model_validate(data)
    except ValueError as exc:
        raise SequenceError(f"Invalid clip settings: {exc}") from exc
    idx = seq.clips.index(clip)
    seq.clips[idx] = updated
    save_sequence(seq)
    return updated


def delete_clip(sequence_id: str, clip_id: str) -> None:
    seq = load_sequence(sequence_id)
    clip = seq.get_clip(clip_id)
    if clip is None:
        raise SequenceError(f"Clip '{clip_id}' not found.")
    cdir = clip_dir(seq, clip)
    seq.clips = [c for c in seq.clips if c.clip_id != clip_id]
    seq.reindex()
    save_sequence(seq)
    # Remove the clip's render folder (assets remain, may be shared).
    if cdir.exists():
        shutil.rmtree(cdir, ignore_errors=True)
    logger.info("Clip removed from sequence '%s': %s", seq.name, clip_id)


def duplicate_clip(sequence_id: str, clip_id: str) -> SequenceClip:
    seq = load_sequence(sequence_id)
    clip = seq.get_clip(clip_id)
    if clip is None:
        raise SequenceError(f"Clip '{clip_id}' not found.")
    from app.models.sequence_models import ClipOutputs, ClipStatus

    copy = clip.model_copy(deep=True)
    copy.clip_id = uuid.uuid4().hex[:10]
    copy.name = f"{clip.name} Copy"
    copy.status = ClipStatus.READY
    copy.progress = 0
    copy.outputs = ClipOutputs()
    copy.diagnostics = None
    copy.last_error = None
    copy.needs_regeneration_reason = None
    copy.seed_used = None
    pos = seq.clips.index(clip) + 1
    seq.clips.insert(pos, copy)
    save_sequence(seq)
    logger.info("Clip duplicated in sequence '%s': %s -> %s", seq.name, clip_id, copy.clip_id)
    return copy


def reorder_clips(sequence_id: str, ordered_ids: list[str]) -> VideoSequence:
    seq = load_sequence(sequence_id)
    by_id = {c.clip_id: c for c in seq.clips}
    if set(ordered_ids) != set(by_id):
        raise SequenceError("Reorder list must contain exactly the existing clip ids.")
    seq.clips = [by_id[cid] for cid in ordered_ids]
    seq.reindex()
    save_sequence(seq)
    return seq


# --------------------------------------------------------------------------
# Assets
# --------------------------------------------------------------------------

def add_image_asset(sequence_id: str, filename: str, data: bytes) -> str:
    from app.services.media_service import validate_image_upload

    seq = load_sequence(sequence_id)
    validate_image_upload(filename, data)
    target = _safe_unique(assets_images_dir(seq), filename)
    (assets_images_dir(seq) / target).write_bytes(data)
    logger.info("Sequence image asset added to '%s': %s (%.1f KB)", seq.name, target, len(data) / 1024)
    return target


def add_audio_asset(sequence_id: str, filename: str, data: bytes, target_clip_id: str | None = None) -> AudioTrack:
    """Save an audio file into the sequence and attach it as a track either to a
    clip (target_clip_id) or to the sequence master tracks."""
    from app.services.audio_service import validate_audio_upload

    seq = load_sequence(sequence_id)
    ext = validate_audio_upload(filename, len(data))
    target = _safe_unique(assets_audio_dir(seq), filename)
    (assets_audio_dir(seq) / target).write_bytes(data)
    track = AudioTrack(
        id=uuid.uuid4().hex[:10],
        filename=target,
        original_filename=Path(filename).name,
        volume=settings.audio_default_volume,
        fade_in=settings.audio_default_fade_in,
        fade_out=settings.audio_default_fade_out,
        loop=settings.audio_default_loop,
        trim_to_video=settings.audio_default_trim_to_video,
    )
    if target_clip_id:
        clip = seq.get_clip(target_clip_id)
        if clip is None:
            raise SequenceError(f"Clip '{target_clip_id}' not found.")
        clip.clip_audio_tracks.append(track)
    else:
        seq.sequence_audio_tracks.append(track)
    save_sequence(seq)
    logger.info("Sequence audio asset added to '%s': %s (clip=%s)", seq.name, target, target_clip_id or "master")
    return track


def _safe_unique(directory: Path, original: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(original).stem).strip("._-") or "asset"
    ext = Path(original).suffix.lower()
    candidate = f"{stem}{ext}"
    counter = 1
    while (directory / candidate).exists():
        candidate = f"{stem}_{counter}{ext}"
        counter += 1
    return candidate


# --------------------------------------------------------------------------
# Effective settings -> transient Project the render backend consumes
# --------------------------------------------------------------------------

def resolve_clip_settings(seq: VideoSequence, clip: SequenceClip) -> dict:
    """Merge global generation settings with the clip's overrides into a flat
    dict of effective values. No hidden values — this is exactly what renders."""
    g = seq.global_generation_settings
    o = clip.generation_overrides if not clip.use_global_generation_settings else ClipGenerationOverrides()

    def pick(attr, gval):
        v = getattr(o, attr, None)
        return v if v is not None else gval

    neg = o.negative_prompt if (o.negative_prompt is not None) else g.negative_prompt
    neg = clip.negative_prompt or neg
    seed_mode = pick("seed_mode", g.seed_mode)
    seed = pick("seed", g.seed)
    return {
        "model_id": g.model_id,
        "mode": "image2video" if clip.type == ClipType.IMAGE_REFERENCE else "text2video",
        "orientation": g.orientation,
        "width": pick("width", g.width),
        "height": pick("height", g.height),
        "frames": pick("frames", g.frames),
        "fps": pick("fps", g.fps),
        "steps": pick("steps", g.steps),
        "guidance_scale": pick("guidance_scale", g.guidance_scale),
        "sampler_name": pick("sampler_name", g.sampler_name),
        "scheduler": pick("scheduler", g.scheduler),
        "denoise": pick("denoise", g.denoise),
        "seed_mode": seed_mode,
        "seed": seed,
        "control_after_generate": getattr(g, "control_after_generate", "fixed"),
        "model_sampling_enabled": g.model_sampling_enabled,
        "model_sampling_shift": pick("model_sampling_shift", g.model_sampling_shift),
        "precision": g.precision,
        "device": g.device,
        "memory_optimization": g.memory_optimization,
        "model_offload": g.model_offload,
        "save_intermediate_frames": getattr(g, "save_intermediate_frames", False),
        "unload_model_after_generation": g.unload_model_after_generation,
        "negative_prompt": neg,
        "wan_preset": g.wan_preset,
    }


def stage_source_image(seq: VideoSequence, clip: SequenceClip) -> str | None:
    """Copy the clip's reference image into the sequence's source/ folder (where
    the generation backend looks up image2video inputs) and return its name."""
    if clip.type != ClipType.IMAGE_REFERENCE or not clip.source_image:
        return None
    src = assets_images_dir(seq) / Path(clip.source_image).name
    if not src.exists():
        raise SequenceError(f"Reference image '{clip.source_image}' is missing from the sequence assets.")
    source_dir = sequence_dir(seq.folder) / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    dst = source_dir / src.name
    if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
        shutil.copy2(src, dst)
    return src.name


def build_clip_project(seq: VideoSequence, clip: SequenceClip) -> tuple[Project, int]:
    """Build an in-memory Project carrying the clip's effective settings, plus
    the resolved seed. This is NOT saved — it only feeds backend.generate()."""
    import random as _random

    s = resolve_clip_settings(seq, clip)
    source_image = stage_source_image(seq, clip)

    if s["seed_mode"] == "fixed" and int(s["seed"]) >= 0:
        seed = int(s["seed"])
    else:
        seed = _random.randint(0, 2**31 - 1)

    params = GenerationParams(
        fps=s["fps"],
        frames=s["frames"],
        seed=seed,
        random_seed=(s["seed_mode"] != "fixed"),
        guidance_scale=s["guidance_scale"],
        sampler_name=s["sampler_name"],
        scheduler=s["scheduler"],
        denoise=s["denoise"],
        control_after_generate=s.get("control_after_generate", "fixed"),
        output_format="mp4",
    )
    params.advanced.steps = s["steps"]
    params.advanced.precision = s["precision"]
    params.advanced.device = s["device"]
    params.advanced.memory_optimization = s["memory_optimization"]
    params.advanced.model_offload = s["model_offload"]
    params.advanced.save_intermediate_frames = s.get("save_intermediate_frames", False)
    params.advanced.unload_model_after_generation = s["unload_model_after_generation"]
    params.model_sampling.enabled = s["model_sampling_enabled"]
    params.model_sampling.shift = s["model_sampling_shift"]
    params.wan_preset = s["wan_preset"]

    project = Project(
        id=seq.sequence_id,
        project_type="video_sequence",
        name=seq.name,
        folder=seq.folder,  # backend resolves I2V source at <folder>/source/<img>
        generation_mode=GenerationMode(s["mode"]),
        orientation=Orientation(s["orientation"]),
        resolution=Resolution(width=s["width"], height=s["height"]),
        model_id=s["model_id"],
        positive_prompt=clip.prompt,
        negative_prompt=s["negative_prompt"],
        source_image=source_image,
        params=params,
    )
    return project, seed
