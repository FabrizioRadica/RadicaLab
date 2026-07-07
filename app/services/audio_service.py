"""Audio Tracks post-processing: upload, per-track settings, ffmpeg mix + mux.

Audio is applied AFTER Wan generation and never influences inference:

    Wan raw video -> mix enabled audio tracks (volume/start/fade/loop/trim)
                  -> mux mixed audio with the video -> <stem>_final.<ext>

The raw generated video is never modified or overwritten. All ffmpeg calls use
argument lists (no shell), and every failure raises AudioError with a
user-readable message.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import uuid
from pathlib import Path

from app.config import logger, settings
from app.models.project_models import AudioTrack, GeneratedVideoEntry, utc_now
from app.services import project_service

_SAFE_STEM_RE = re.compile(r"[^A-Za-z0-9._-]+")
_DURATION_RE = re.compile(rb"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")

FINAL_SUFFIX = "_final"


class AudioError(Exception):
    """Raised for audio post-processing failures with a user-readable message."""


# --------------------------------------------------------------------------
# ffmpeg resolution and probing
# --------------------------------------------------------------------------

def resolve_ffmpeg() -> str:
    """Locate ffmpeg: FFMPEG_PATH -> system PATH -> bundled imageio-ffmpeg."""
    configured = settings.ffmpeg_path
    if configured and configured != "ffmpeg":
        if Path(configured).exists():
            return configured
        raise AudioError(f"FFMPEG_PATH points to '{configured}' but the file does not exist.")
    found = shutil.which(configured or "ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # noqa: BLE001 — missing binary must become a clear error
        raise AudioError(
            "FFmpeg is required to mix and apply audio tracks. Install FFmpeg and "
            "ensure it is available in PATH (or set FFMPEG_PATH in .env)."
        ) from exc


def media_duration(path: Path) -> float:
    """Duration in seconds of a media file, parsed from ffmpeg output."""
    ffmpeg = resolve_ffmpeg()
    proc = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)],
                          capture_output=True, timeout=60)
    match = _DURATION_RE.search(proc.stderr)
    if not match:
        raise AudioError(f"Could not read the duration of '{path.name}' — the file "
                         "may be corrupted or in an unsupported format.")
    h, m, s = int(match.group(1)), int(match.group(2)), float(match.group(3))
    return h * 3600 + m * 60 + s


# --------------------------------------------------------------------------
# Upload / track management
# --------------------------------------------------------------------------

def _audio_dir(project) -> Path:
    d = project_service.project_dir(project.folder) / "audio"
    d.mkdir(exist_ok=True)
    return d


def validate_audio_upload(filename: str, size: int) -> str:
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext not in settings.allowed_audio_extensions:
        allowed = ", ".join(settings.allowed_audio_extensions)
        raise AudioError(f"Audio type '.{ext}' is not allowed. Allowed types: {allowed}.")
    max_bytes = settings.max_audio_upload_size_mb * 1024 * 1024
    if size > max_bytes:
        raise AudioError(f"Audio file is too large ({size / 1024 / 1024:.1f} MB). "
                         f"Limit is {settings.max_audio_upload_size_mb} MB.")
    if size == 0:
        raise AudioError("The uploaded audio file is empty.")
    return ext


def _safe_unique_name(audio_dir: Path, original: str, ext: str) -> str:
    stem = _SAFE_STEM_RE.sub("_", Path(original).stem).strip("._-") or "track"
    candidate = f"{stem}.{ext}"
    counter = 1
    while (audio_dir / candidate).exists():
        candidate = f"{stem}_{counter}.{ext}"
        counter += 1
    return candidate


def add_track(project_id: str, filename: str, data: bytes) -> AudioTrack:
    project = project_service.load_project(project_id)
    ext = validate_audio_upload(filename, len(data))
    audio_dir = _audio_dir(project)
    target = _safe_unique_name(audio_dir, filename, ext)
    (audio_dir / target).write_bytes(data)
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
    project.audio_tracks.append(track)
    project_service.save_project(project)
    logger.info("Audio track added to '%s': %s (%.1f KB)", project.name, target, len(data) / 1024)
    return track


EDITABLE_TRACK_FIELDS = ("enabled", "volume", "start_time", "fade_in",
                         "fade_out", "loop", "trim_to_video")


def update_track(project_id: str, track_id: str, changes: dict) -> AudioTrack:
    project = project_service.load_project(project_id)
    for i, track in enumerate(project.audio_tracks):
        if track.id == track_id:
            merged = track.model_dump()
            for key in EDITABLE_TRACK_FIELDS:
                if key in changes:
                    merged[key] = changes[key]
            merged["updated_at"] = utc_now()
            try:
                updated = AudioTrack.model_validate(merged)
            except ValueError as exc:
                raise AudioError(f"Invalid track settings: {exc}") from exc
            project.audio_tracks[i] = updated
            project_service.save_project(project)
            logger.info("Audio track updated: %s (%s)", track_id, track.filename)
            return updated
    raise AudioError(f"Audio track '{track_id}' not found in this project.")


def remove_track(project_id: str, track_id: str) -> None:
    project = project_service.load_project(project_id)
    track = next((t for t in project.audio_tracks if t.id == track_id), None)
    if track is None:
        raise AudioError(f"Audio track '{track_id}' not found in this project.")
    project.audio_tracks = [t for t in project.audio_tracks if t.id != track_id]
    # Delete the file only when no remaining track references it.
    if not any(t.filename == track.filename for t in project.audio_tracks):
        path = _audio_dir(project) / Path(track.filename).name
        path.unlink(missing_ok=True)
    project_service.save_project(project)
    logger.info("Audio track removed: %s (%s)", track_id, track.filename)


# --------------------------------------------------------------------------
# Mixing and muxing
# --------------------------------------------------------------------------

def _track_filter(index: int, track: AudioTrack, audio_len: float,
                  video_len: float) -> str:
    """Filter chain for one track: trim/loop cap -> volume -> fades -> delay."""
    available = video_len - track.start_time
    if available <= 0:
        raise AudioError(
            f"Track '{track.filename}': start time ({track.start_time}s) is beyond "
            f"the video duration ({video_len:.2f}s)."
        )
    if track.loop or track.trim_to_video:
        play_len = min(audio_len, available) if not track.loop else available
    else:
        play_len = audio_len

    if track.fade_in + track.fade_out > play_len + 0.01:
        raise AudioError(
            f"Track '{track.filename}': fade in ({track.fade_in}s) plus fade out "
            f"({track.fade_out}s) exceed the track's play length ({play_len:.2f}s)."
        )

    steps = []
    if track.loop or (track.trim_to_video and audio_len > available):
        steps.append(f"atrim=0:{play_len:.3f}")
    steps.append(f"volume={track.volume:.3f}")
    if track.fade_in > 0:
        steps.append(f"afade=t=in:st=0:d={track.fade_in:.3f}")
    if track.fade_out > 0:
        steps.append(f"afade=t=out:st={max(play_len - track.fade_out, 0):.3f}:d={track.fade_out:.3f}")
    if track.start_time > 0:
        steps.append(f"adelay={int(track.start_time * 1000)}:all=1")
    steps.append("aresample=48000")
    return f"[{index}:a]" + ",".join(steps) + f"[a{index}]"


def apply_audio(project_id: str, video_filename: str) -> GeneratedVideoEntry:
    """Mix all enabled tracks and mux them with the given raw video.

    Writes <stem>_final.<ext> next to the raw output (never touching it),
    plus a metadata JSON, and records the final video in the project.
    """
    import json

    project = project_service.load_project(project_id)
    pdir = project_service.project_dir(project.folder)
    video_name = Path(video_filename).name
    if video_name != video_filename or not video_name:
        raise AudioError("Invalid video file name.")
    video_path = pdir / "outputs" / video_name
    if not video_path.exists():
        raise AudioError(f"Video '{video_name}' was not found in the project outputs folder.")
    if FINAL_SUFFIX in video_path.stem:
        raise AudioError("Pick a raw generated video — this file already contains mixed audio.")

    tracks = [t for t in project.audio_tracks if t.enabled]
    if not tracks:
        raise AudioError("No enabled audio tracks to apply.")
    audio_dir = _audio_dir(project)
    missing = [t.filename for t in tracks if not (audio_dir / Path(t.filename).name).exists()]
    if missing:
        raise AudioError("Audio file(s) missing from the project audio folder: "
                         + ", ".join(missing) + ". Re-upload them or remove the tracks.")

    ffmpeg = resolve_ffmpeg()
    video_len = media_duration(video_path)

    cmd = [ffmpeg, "-hide_banner", "-y", "-i", str(video_path)]
    filters = []
    for i, track in enumerate(tracks, start=1):
        audio_path = audio_dir / Path(track.filename).name
        audio_len = media_duration(audio_path)
        if track.loop:
            cmd += ["-stream_loop", "-1"]
        cmd += ["-i", str(audio_path)]
        filters.append(_track_filter(i, track, audio_len, video_len))

    if len(tracks) > 1:
        labels = "".join(f"[a{i}]" for i in range(1, len(tracks) + 1))
        filters.append(f"{labels}amix=inputs={len(tracks)}:duration=longest:"
                       "dropout_transition=0:normalize=0[aout]")
        out_label = "[aout]"
    else:
        out_label = "[a1]"

    final_name = f"{video_path.stem}{FINAL_SUFFIX}{video_path.suffix}"
    final_path = video_path.parent / final_name
    acodec = ["-c:a", "libopus", "-b:a", "160k"] if video_path.suffix.lower() == ".webm" \
        else ["-c:a", "aac", "-b:a", "192k"]
    cmd += ["-filter_complex", ";".join(filters), "-map", "0:v", "-map", out_label,
            "-c:v", "copy", *acodec, str(final_path)]

    logger.info("Audio mix started: %s + %d track(s) -> %s", video_name, len(tracks), final_name)
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=600)
    except subprocess.TimeoutExpired as exc:
        raise AudioError("Audio processing timed out after 10 minutes.") from exc
    if proc.returncode != 0 or not final_path.exists() or final_path.stat().st_size == 0:
        tail = proc.stderr.decode("utf-8", errors="replace").strip().splitlines()[-6:]
        final_path.unlink(missing_ok=True)  # never leave a broken final file behind
        logger.error("Audio mix failed for %s: %s", video_name, " | ".join(tail))
        raise AudioError("FFmpeg failed to mix/mux the audio: " + (" ".join(tail) or "unknown error"))
    logger.info("Audio mux success: %s (%.1f KB)", final_name, final_path.stat().st_size / 1024)

    # --- metadata: extend the raw video's metadata, never replacing it -----
    raw_entry = next((v for v in project.generated_videos if v.filename == video_name), None)
    meta_dir = pdir / "metadata"
    meta_dir.mkdir(exist_ok=True)
    base_meta = {}
    if raw_entry and raw_entry.metadata_file and (meta_dir / raw_entry.metadata_file).exists():
        try:
            base_meta = json.loads((meta_dir / raw_entry.metadata_file).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            base_meta = {}
    base_meta.update({
        "has_audio": True,
        "audio_mix_applied": True,
        "raw_output_file": video_name,
        "final_output_file": final_name,
        "output_file": final_name,
        "audio_tracks": [t.model_dump(mode="json") for t in tracks],
        "audio_applied_at": utc_now(),
    })
    final_meta_name = f"{video_path.stem}{FINAL_SUFFIX}.json"
    (meta_dir / final_meta_name).write_text(json.dumps(base_meta, indent=2, ensure_ascii=False),
                                            encoding="utf-8")

    entry = GeneratedVideoEntry(
        filename=final_name,
        preview=raw_entry.preview if raw_entry else None,
        metadata_file=final_meta_name,
        mode=raw_entry.mode if raw_entry else project.generation_mode.value,
        model_id=raw_entry.model_id if raw_entry else project.model_id,
        model_bundle_id=raw_entry.model_bundle_id if raw_entry else "",
        model_bundle_name=raw_entry.model_bundle_name if raw_entry else "",
        resolution=raw_entry.resolution if raw_entry else project.resolution.label(),
        fps=raw_entry.fps if raw_entry else project.params.fps,
        frames=raw_entry.frames if raw_entry else project.params.frames,
        seed=raw_entry.seed if raw_entry else None,
        is_mock=raw_entry.is_mock if raw_entry else False,
        has_audio=True,
        raw_filename=video_name,
    )
    # Re-applying audio to the same raw video replaces its previous final entry.
    fresh = project_service.load_project(project_id)
    fresh.generated_videos = [v for v in fresh.generated_videos if v.filename != final_name]
    fresh.generated_videos.append(entry)
    project_service.save_project(fresh)
    return entry
