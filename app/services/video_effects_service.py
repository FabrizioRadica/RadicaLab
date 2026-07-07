"""Video Effects / Color & Look post-processing (ffmpeg-based).

Applied AFTER Wan generation — never part of inference:

    Wan raw video -> color & look filter chain -> <stem>_fx.<ext>

The raw generated video is never modified. Audio post-processing can then be
applied on top of the _fx output, giving the full pipeline
raw -> effects -> audio -> final.

Effect mapping notes (kept in sync with the canvas preview in
app/static/js/video_effects.js — both are approximations of the same look):

- saturation/contrast/brightness/gamma -> eq filter
- hue                                  -> hue filter (degrees)
- temperature                          -> colortemperature filter (Kelvin)
- shadows/highlights                   -> curves filter (piecewise tone curve)
- sharpness                            -> unsharp
- vignette                             -> vignette filter; radius/softness
                                          modulate the vignette angle (the
                                          ffmpeg filter has no radius param)
- film grain                           -> noise filter (grain_size only
                                          affects the browser preview)
- VHS                                  -> rgbashift (chromatic aberration),
                                          chroma boxblur (color bleeding),
                                          noise (noise + tape damage),
                                          drawgrid (scanlines), animated crop
                                          (jitter + tracking), eq desaturation
"""

from __future__ import annotations

import math
import re
import subprocess
from pathlib import Path

from app.config import logger, settings
from app.models.project_models import GeneratedVideoEntry, VideoEffects, utc_now
from app.services import project_service
from app.services.audio_service import AudioError, media_duration, resolve_ffmpeg

FX_SUFFIX = "_fx"
_RESOLUTION_RE = re.compile(rb"Video:.*?\s(\d{2,5})x(\d{2,5})")


class VideoEffectsError(Exception):
    """Raised for video-effects failures with a user-readable message."""


def get_effects(project_id: str) -> VideoEffects:
    return project_service.load_project(project_id).video_effects


def update_effects(project_id: str, changes: dict) -> VideoEffects:
    """Deep-merge partial changes into the project's video effects settings."""
    project = project_service.load_project(project_id)
    merged = project.video_effects.model_dump()
    for key, value in (changes or {}).items():
        if key in ("vignette", "film_grain", "sharpness", "vhs_effect") and isinstance(value, dict):
            merged.setdefault(key, {}).update(value)
        elif key in merged:
            merged[key] = value
    try:
        project.video_effects = VideoEffects.model_validate(merged)
    except ValueError as exc:
        raise VideoEffectsError(f"Invalid video effect settings: {exc}") from exc
    project_service.save_project(project)
    logger.info("Video effects updated for '%s' (enabled=%s)", project.name,
                project.video_effects.enabled)
    return project.video_effects


# --------------------------------------------------------------------------
# ffmpeg filter graph
# --------------------------------------------------------------------------

def _curves_points(shadows: float, highlights: float) -> str:
    """Piecewise tone curve approximating shadow lift/crush and highlight
    boost/roll-off. Points are kept strictly increasing in x and clamped."""
    s, h = shadows / 100.0, highlights / 100.0
    pts = [
        (0.0, max(s, 0.0) * 0.18),
        (0.28, min(max(0.28 + s * 0.12, 0.02), 0.55)),
        (0.72, min(max(0.72 + h * 0.12, 0.45), 0.98)),
        (1.0, 1.0 + min(h, 0.0) * 0.18),
    ]
    return " ".join(f"{x:.3f}/{min(max(y, 0.0), 1.0):.3f}" for x, y in pts)


def build_filter_chain(fx: VideoEffects, width: int, height: int) -> list[str]:
    """The ffmpeg -vf filter list for the given settings (master toggle and
    per-effect toggles already honored)."""
    filters: list[str] = []

    eq_parts = []
    if fx.saturation != 1.0:
        eq_parts.append(f"saturation={fx.saturation:.3f}")
    if fx.contrast != 1.0:
        eq_parts.append(f"contrast={fx.contrast:.3f}")
    if fx.brightness != 0.0:
        eq_parts.append(f"brightness={fx.brightness / 100.0 * 0.5:.3f}")
    if fx.gamma != 1.0:
        eq_parts.append(f"gamma={fx.gamma:.3f}")
    if eq_parts:
        filters.append("eq=" + ":".join(eq_parts))

    if fx.hue != 0.0:
        filters.append(f"hue=h={fx.hue:.1f}")

    if fx.temperature != 0.0:
        # -100 (cool, ~9500K) .. 0 (neutral 6500K) .. +100 (warm, ~3500K)
        kelvin = int(6500 - fx.temperature * 30)
        filters.append(f"colortemperature=temperature={kelvin}")

    if fx.shadows != 0.0 or fx.highlights != 0.0:
        filters.append(f"curves=all='{_curves_points(fx.shadows, fx.highlights)}'")

    if fx.sharpness.enabled and fx.sharpness.amount > 0:
        filters.append(f"unsharp=5:5:{fx.sharpness.amount:.3f}:5:5:0")

    if fx.vignette.enabled and fx.vignette.intensity > 0:
        v = fx.vignette
        strength = v.intensity * (1.15 - 0.7 * v.radius) * (0.7 + 0.6 * v.softness)
        angle = min(max(strength, 0.02), 1.0) * math.pi / 2
        filters.append(f"vignette=angle={angle:.4f}")

    if fx.film_grain.enabled and fx.film_grain.intensity > 0:
        g = fx.film_grain
        flags = "t+u" if g.animated else "u"
        filters.append(f"noise=alls={max(int(g.intensity * 40), 1)}:allf={flags}")

    if fx.vhs_effect.enabled and fx.vhs_effect.intensity > 0:
        m = fx.vhs_effect.intensity
        vhs = fx.vhs_effect
        ca_px = round(vhs.chromatic_aberration * m * 8)
        if ca_px > 0:
            filters.append(f"rgbashift=rh={ca_px}:bh={-ca_px}")
        cb_r = round(vhs.color_bleeding * m * 4)
        if cb_r > 0:
            filters.append(f"boxblur=0:0:{cb_r}:1")
        vhs_noise = max(int((vhs.noise * 0.7 + vhs.tape_damage * 0.6) * m * 55), 0)
        if vhs_noise > 0:
            filters.append(f"noise=alls={vhs_noise}:allf=t+u")
        jx = round(vhs.jitter * m * 10)
        ty = round(vhs.tracking_distortion * m * 12)
        if jx > 0 or ty > 0:
            # Animated crop: horizontal jitter + slow vertical tracking roll,
            # scaled back to the original resolution afterwards.
            cw, ch = width - 2 * jx, height - 2 * ty
            x_expr = f"{jx}+{jx}*sin(n*2.1)" if jx > 0 else "0"
            y_expr = f"{ty}+{ty}*sin(n*0.37)" if ty > 0 else "0"
            filters.append(f"crop={cw}:{ch}:x='{x_expr}':y='{y_expr}'")
            filters.append(f"scale={width}:{height}")
        if vhs.scanlines > 0:
            opacity = min(vhs.scanlines * m, 1.0) * 0.4
            filters.append(f"drawgrid=w=iw:h=3:t=1:color=black@{opacity:.3f}")
        filters.append(f"eq=saturation={1 - 0.25 * m:.3f}:contrast={1 + 0.05 * m:.3f}")

    return filters


def _probe_resolution(ffmpeg: str, path: Path) -> tuple[int, int]:
    proc = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)],
                          capture_output=True, timeout=60)
    match = _RESOLUTION_RE.search(proc.stderr)
    if not match:
        raise VideoEffectsError(f"Could not read the resolution of '{path.name}'.")
    return int(match.group(1)), int(match.group(2))


# --------------------------------------------------------------------------
# Apply
# --------------------------------------------------------------------------

def apply_effects(project_id: str, video_filename: str) -> GeneratedVideoEntry:
    """Render the Color & Look chain onto a raw generated video.

    Writes <stem>_fx.<ext> (the raw file is never touched), extends the raw
    metadata with the effect snapshot and records the new video in the project.
    """
    import json

    project = project_service.load_project(project_id)
    fx = project.video_effects
    if not fx.enabled:
        raise VideoEffectsError("Video effects are disabled for this project. "
                                "Enable them in the Color & Look panel first.")

    pdir = project_service.project_dir(project.folder)
    video_name = Path(video_filename).name
    if video_name != video_filename or not video_name:
        raise VideoEffectsError("Invalid video file name.")
    video_path = pdir / "outputs" / video_name
    if not video_path.exists():
        raise VideoEffectsError(f"Video '{video_name}' was not found in the project outputs folder.")
    if FX_SUFFIX in video_path.stem or "_final" in video_path.stem:
        raise VideoEffectsError("Pick a raw generated video — this file is already post-processed.")

    try:
        ffmpeg = resolve_ffmpeg()
    except AudioError as exc:
        raise VideoEffectsError(str(exc)) from exc
    width, height = _probe_resolution(ffmpeg, video_path)

    filters = build_filter_chain(fx, width, height)
    if not filters:
        raise VideoEffectsError("All effect values are at their neutral defaults — "
                                "there is nothing to apply.")

    out_name = f"{video_path.stem}{FX_SUFFIX}{video_path.suffix}"
    out_path = video_path.parent / out_name
    vcodec = ["-c:v", "libvpx-vp9", "-b:v", "2M"] if video_path.suffix.lower() == ".webm" \
        else ["-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p"]
    cmd = [ffmpeg, "-hide_banner", "-y", "-i", str(video_path),
           "-vf", ",".join(filters), "-map", "0", "-c:a", "copy", *vcodec, str(out_path)]

    logger.info("Video effects apply started: %s (%d filter(s)) -> %s",
                video_name, len(filters), out_name)
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=900)
    except subprocess.TimeoutExpired as exc:
        out_path.unlink(missing_ok=True)
        raise VideoEffectsError("Video effects processing timed out after 15 minutes.") from exc
    if proc.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
        tail = proc.stderr.decode("utf-8", errors="replace").strip().splitlines()[-6:]
        out_path.unlink(missing_ok=True)
        logger.error("Video effects apply failed for %s: %s", video_name, " | ".join(tail))
        raise VideoEffectsError("FFmpeg failed to apply the video effects: "
                                + (" ".join(tail) or "unknown error"))
    logger.info("Video effects apply success: %s (%.1f KB)",
                out_name, out_path.stat().st_size / 1024)

    # --- metadata: extend the raw video's metadata ---------------------------
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
        "video_effects_applied": True,
        "video_effects": fx.model_dump(mode="json"),
        "raw_output_file": video_name,
        "final_output_file": out_name,
        "output_file": out_name,
        "effects_applied_at": utc_now(),
    })
    meta_name = f"{video_path.stem}{FX_SUFFIX}.json"
    (meta_dir / meta_name).write_text(json.dumps(base_meta, indent=2, ensure_ascii=False),
                                      encoding="utf-8")

    try:
        duration = media_duration(out_path)
    except AudioError:
        duration = None
    logger.info("Video effects output duration: %s", duration)

    entry = GeneratedVideoEntry(
        filename=out_name,
        preview=raw_entry.preview if raw_entry else None,
        metadata_file=meta_name,
        mode=raw_entry.mode if raw_entry else project.generation_mode.value,
        model_id=raw_entry.model_id if raw_entry else project.model_id,
        model_bundle_id=raw_entry.model_bundle_id if raw_entry else "",
        model_bundle_name=raw_entry.model_bundle_name if raw_entry else "",
        resolution=raw_entry.resolution if raw_entry else f"{width}x{height}",
        fps=raw_entry.fps if raw_entry else project.params.fps,
        frames=raw_entry.frames if raw_entry else project.params.frames,
        seed=raw_entry.seed if raw_entry else None,
        is_mock=raw_entry.is_mock if raw_entry else False,
        has_effects=True,
        raw_filename=video_name,
    )
    # Re-applying effects to the same raw video replaces its previous fx entry.
    fresh = project_service.load_project(project_id)
    fresh.generated_videos = [v for v in fresh.generated_videos if v.filename != out_name]
    fresh.generated_videos.append(entry)
    project_service.save_project(fresh)
    return entry
