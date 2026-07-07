"""Project (.wanproj) data models."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from app.models.camera_motion_models import CameraMotionSettings
from app.models.generation_models import GenerationMode, GenerationParams, Orientation, Resolution

WANPROJ_SCHEMA = "wanproj/1"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class GeneratedVideoEntry(BaseModel):
    filename: str
    preview: str | None = None
    metadata_file: str | None = None
    mode: str = "text2video"
    model_id: str = ""
    model_bundle_id: str = ""
    model_bundle_name: str = ""
    model_bundle_snapshot: dict | None = None
    resolution: str = ""
    fps: int = 24
    frames: int = 81
    seed: int | None = None
    is_mock: bool = False
    # Post-processing: final videos carry mixed audio and/or video effects and
    # point back to the untouched raw Wan output they were created from.
    has_audio: bool = False
    has_effects: bool = False
    raw_filename: str | None = None
    created_at: str = Field(default_factory=utc_now)


class VignetteSettings(BaseModel):
    enabled: bool = False
    intensity: float = Field(default=0.25, ge=0.0, le=1.0)
    radius: float = Field(default=0.75, ge=0.0, le=1.0)
    softness: float = Field(default=0.5, ge=0.0, le=1.0)


class FilmGrainSettings(BaseModel):
    enabled: bool = False
    intensity: float = Field(default=0.15, ge=0.0, le=1.0)
    grain_size: float = Field(default=0.5, ge=0.0, le=1.0)
    animated: bool = True


class SharpnessSettings(BaseModel):
    enabled: bool = False
    amount: float = Field(default=1.0, ge=0.0, le=2.0)


class VhsEffectSettings(BaseModel):
    enabled: bool = False
    intensity: float = Field(default=0.3, ge=0.0, le=1.0)
    scanlines: float = Field(default=0.2, ge=0.0, le=1.0)
    chromatic_aberration: float = Field(default=0.15, ge=0.0, le=1.0)
    noise: float = Field(default=0.15, ge=0.0, le=1.0)
    jitter: float = Field(default=0.1, ge=0.0, le=1.0)
    tracking_distortion: float = Field(default=0.1, ge=0.0, le=1.0)
    color_bleeding: float = Field(default=0.15, ge=0.0, le=1.0)
    tape_damage: float = Field(default=0.05, ge=0.0, le=1.0)


class VideoEffects(BaseModel):
    """Color & Look post-processing settings — applied AFTER Wan generation,
    never part of inference. Disabled by default."""

    enabled: bool = False
    saturation: float = Field(default=1.0, ge=0.0, le=2.0)
    contrast: float = Field(default=1.0, ge=0.0, le=2.0)
    hue: float = Field(default=0.0, ge=-180.0, le=180.0)
    temperature: float = Field(default=0.0, ge=-100.0, le=100.0)
    shadows: float = Field(default=0.0, ge=-100.0, le=100.0)
    highlights: float = Field(default=0.0, ge=-100.0, le=100.0)
    brightness: float = Field(default=0.0, ge=-100.0, le=100.0)
    gamma: float = Field(default=1.0, ge=0.1, le=3.0)
    vignette: VignetteSettings = Field(default_factory=VignetteSettings)
    film_grain: FilmGrainSettings = Field(default_factory=FilmGrainSettings)
    sharpness: SharpnessSettings = Field(default_factory=SharpnessSettings)
    vhs_effect: VhsEffectSettings = Field(default_factory=VhsEffectSettings)


class AudioTrack(BaseModel):
    """One uploaded audio track, mixed into the video as post-processing.

    Audio never influences Wan inference — it is applied to the finished
    video with ffmpeg (mix + mux).
    """

    id: str
    filename: str
    original_filename: str = ""
    enabled: bool = True
    volume: float = Field(default=1.0, ge=0.0, le=2.0)
    start_time: float = Field(default=0.0, ge=0.0)
    fade_in: float = Field(default=0.0, ge=0.0)
    fade_out: float = Field(default=0.0, ge=0.0)
    loop: bool = False
    trim_to_video: bool = True
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)


class ExportedWorkflowEntry(BaseModel):
    filename: str
    path: str
    created_at: str = Field(default_factory=utc_now)


class ProjectCredits(BaseModel):
    application: str = "Radica - WanVideoGenerator"
    concept_and_design: str = "Fabrizio Radica"
    project_by: str = "RadicaDesign"


class Project(BaseModel):
    schema_version: str = Field(default=WANPROJ_SCHEMA, alias="schema")
    id: str
    # Distinguishes single-clip projects from video sequences (patchRC2 §12).
    # Existing projects have no value and default to "single_clip".
    project_type: str = "single_clip"
    name: str
    folder: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
    generation_mode: GenerationMode = GenerationMode.TEXT2VIDEO
    orientation: Orientation = Orientation.LANDSCAPE
    resolution: Resolution = Field(default_factory=lambda: Resolution(width=1280, height=720))
    model_id: str = ""
    positive_prompt: str = ""
    negative_prompt: str = ""
    source_image: str | None = None
    params: GenerationParams = Field(default_factory=GenerationParams)
    camera_motion: CameraMotionSettings = Field(default_factory=CameraMotionSettings)
    generated_videos: list[GeneratedVideoEntry] = Field(default_factory=list)
    audio_tracks: list[AudioTrack] = Field(default_factory=list)
    video_effects: VideoEffects = Field(default_factory=VideoEffects)
    exported_workflows: list[ExportedWorkflowEntry] = Field(default_factory=list)
    credits: ProjectCredits = Field(default_factory=ProjectCredits)
    app_version: str = "1.0.0"

    model_config = {"populate_by_name": True}

    def composed_prompt(self) -> str:
        """Final prompt = user positive prompt + camera motion fragment (when applied)."""
        parts = [self.positive_prompt.strip()]
        if self.camera_motion.enabled and self.camera_motion.applied_to_prompt and self.camera_motion.fragment:
            parts.append(self.camera_motion.fragment.strip())
        return ", ".join(p for p in parts if p)

    def duration_seconds(self) -> float:
        """Video duration derived from the frame-based params: frames / fps."""
        return round(self.params.frames / max(self.params.fps, 1), 3)

    def to_wanproj_dict(self) -> dict[str, Any]:
        data = self.model_dump(mode="json", by_alias=True)
        # Stored explicitly for readability; always derived from frames/fps.
        data["duration_seconds"] = self.duration_seconds()
        return data


class ProjectSummary(BaseModel):
    id: str
    name: str
    folder: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    generation_mode: str = "text2video"
    orientation: str = "landscape"
    resolution: str = "1280x720"
    fps: int = 24
    frames: int = 81
    model_id: str = ""
    video_count: int = 0
    thumbnail: str | None = None
    created_at: str = ""
    updated_at: str = ""
