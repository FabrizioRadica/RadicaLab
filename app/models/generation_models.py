"""Generation parameter and job models."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field, model_validator


class GenerationMode(str, Enum):
    TEXT2VIDEO = "text2video"
    IMAGE2VIDEO = "image2video"


class Orientation(str, Enum):
    LANDSCAPE = "landscape"
    PORTRAIT = "portrait"
    SQUARE = "square"


class Resolution(BaseModel):
    width: int = Field(ge=16, le=8192)
    height: int = Field(ge=16, le=8192)

    def label(self) -> str:
        return f"{self.width}x{self.height}"


# ComfyUI/KSampler-compatible choice lists. Actual support depends on the
# selected backend / ComfyUI installation — unsupported values are kept in
# metadata and reported as ignored, never silently remapped.
CONTROL_AFTER_GENERATE_VALUES = ("fixed", "randomize", "increment", "decrement")

SAMPLER_NAMES = (
    "euler", "euler_ancestral", "heun", "dpm_2", "dpm_2_ancestral", "lms",
    "dpm_fast", "dpm_adaptive", "dpmpp_2m", "dpmpp_sde", "dpmpp_2m_sde",
    "ddim", "uni_pc",
)

SCHEDULER_NAMES = (
    "simple", "normal", "karras", "exponential", "sgm_uniform", "beta",
    "ddim_uniform",
)

# Quality presets (patch6 §12/§13). "none" = use the parameters exactly as set.
# "comfyui_match" prioritizes matching ComfyUI; "high_quality" prioritizes
# visual quality over speed. Neither silently overrides user parameters — every
# change is reported in the job log, metadata and diagnostics panel.
QUALITY_PRESETS = ("none", "comfyui_match", "high_quality")

# Legacy advanced.sampler values mapped to their KSampler-compatible names.
_LEGACY_SAMPLER_MAP = {"unipc": "uni_pc", "dpm++": "dpmpp_2m"}


# Model sampling modifier types (patch7). Currently only SD3 (flow-matching
# shift), which is what the ComfyUI ModelSamplingSD3 node applies to Wan.
MODEL_SAMPLING_TYPES = ("sd3",)


class ModelSamplingParams(BaseModel):
    """ModelSamplingSD3 modifier applied between model loading and sampling
    (patch7 §5/§7). `shift` is the flow-matching sigma shift — NOT CFG, denoise,
    motion strength or image influence. Tracked as its own independent setting."""
    enabled: bool = False
    type: str = "sd3"
    shift: float = Field(default=8.0, ge=0.0, le=100.0)

    @model_validator(mode="after")
    def _normalize(self):
        t = (self.type or "sd3").lower()
        self.type = t if t in MODEL_SAMPLING_TYPES else "sd3"
        return self


class AdvancedParams(BaseModel):
    steps: int = Field(default=30, ge=1, le=200)
    sampler: str = "unipc"
    precision: str = "bf16"
    memory_optimization: bool = True
    device: str = "cuda"
    model_offload: bool = False
    cache_latents: bool = False
    preview_frames: bool = False
    save_intermediate_frames: bool = False
    output_codec: str = "h264"
    # Per-project offload override (patch8): "" = use env WAN_OFFLOAD_POLICY,
    # else balanced | aggressive | disabled. Set by the Low VRAM preset.
    offload_policy: str = ""
    # Per-project "unload model weights after each render" (patch8 §18/§20).
    unload_model_after_generation: bool = False


class GenerationParams(BaseModel):
    fps: int = Field(default=24, ge=1, le=120)
    frames: int = Field(default=81, ge=1, le=1000)
    seed: int = Field(default=-1, ge=-1)
    random_seed: bool = True
    # ComfyUI/KSampler-compatible sampling parameters. guidance_scale is the
    # internal name; it maps to `cfg` in ComfyUI exports and metadata.
    control_after_generate: str = "fixed"
    guidance_scale: float = Field(default=6.0, ge=0.0, le=30.0)
    sampler_name: str = ""
    scheduler: str = "simple"
    # Denoise is a first-class sampling parameter. It is NOT CFG, NOT motion
    # strength and NOT image influence and must never be mapped onto them.
    denoise: float = Field(default=1.0, ge=0.0, le=1.0)
    motion_strength: float = Field(default=0.7, ge=0.0, le=1.0)
    image_influence: float = Field(default=0.85, ge=0.0, le=1.0)
    output_format: str = "mp4"
    # Quality preset (patch6 §12/§13): none | comfyui_match | high_quality.
    quality_preset: str = "none"
    # Wan2.2 5B practical preset (patch8): manual | wan22_5b_fast_preview |
    # wan22_5b_safe_quality | wan22_5b_low_vram. Tracks the last applied preset.
    wan_preset: str = "manual"
    # ModelSamplingSD3 modifier (patch7 §7): its own structured, independent
    # block — never merged into denoise/cfg/motion.
    model_sampling: ModelSamplingParams = Field(default_factory=ModelSamplingParams)
    advanced: AdvancedParams = Field(default_factory=AdvancedParams)

    @model_validator(mode="after")
    def _migrate_and_clamp(self):
        # Older .wanproj files have no sampler_name — derive it from the
        # legacy advanced.sampler value so they keep loading unchanged.
        if not self.sampler_name:
            legacy = (self.advanced.sampler or "").lower()
            self.sampler_name = _LEGACY_SAMPLER_MAP.get(legacy, legacy) or "euler"
        else:
            self.sampler_name = _LEGACY_SAMPLER_MAP.get(self.sampler_name.lower(),
                                                        self.sampler_name.lower())
        if self.control_after_generate not in CONTROL_AFTER_GENERATE_VALUES:
            self.control_after_generate = "fixed"
        if self.scheduler:
            self.scheduler = self.scheduler.lower()
        else:
            self.scheduler = "simple"
        preset = (self.quality_preset or "none").lower()
        self.quality_preset = preset if preset in QUALITY_PRESETS else "none"
        return self


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"


# Statuses during which a job can still be cancelled (patch6b §2/§4).
CANCELLABLE_STATUSES = (JobStatus.PENDING, JobStatus.RUNNING, JobStatus.CANCEL_REQUESTED)


class GenerationJob(BaseModel):
    id: str
    project_id: str
    project_name: str
    mode: GenerationMode
    model_id: str
    status: JobStatus = JobStatus.PENDING
    progress: int = Field(default=0, ge=0, le=100)
    stage: str = "Queued"
    log: list[str] = Field(default_factory=list)
    error: str | None = None
    backend: str = "mock"
    is_mock: bool = True
    # User confirmed an `ask`-policy sampler fallback for this job (patch6c §8).
    confirm_fallback: bool = False
    model_bundle: dict | None = None  # component snapshot resolved at submit time
    seed_used: int | None = None
    # Backend diagnostics (effective settings, device/dtype maps, timings,
    # VRAM, warnings) populated on completion for the Backend Diagnostics panel.
    diagnostics: dict | None = None
    preset_notes: list[str] = Field(default_factory=list)
    # Last VRAM cleanup report for this job (patch8 §14/§16/§21).
    last_vram_cleanup: dict | None = None
    # Cancellation tracking (patch6b §4/§11).
    cancel_requested: bool = False
    cancel_requested_at: str | None = None
    cancelled_at: str | None = None
    output_file: str | None = None
    output_url: str | None = None
    preview_url: str | None = None
    metadata_file: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: str | None = None
    finished_at: str | None = None
