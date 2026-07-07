"""
Radica - WanVideoGenerator — application configuration.

Loads all configurable values from the .env file at startup.
Concept & Design: Fabrizio Radica — Project by RadicaDesign
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"

_env_loaded = load_dotenv(ENV_FILE)


def _str(key: str, default: str) -> str:
    value = os.getenv(key)
    return value.strip() if value else default


def _int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, "").strip())
    except (ValueError, AttributeError):
        return default


def _float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, "").strip())
    except (ValueError, AttributeError):
        return default


def _bool(key: str, default: bool) -> bool:
    value = os.getenv(key)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _path(key: str, default: str) -> Path:
    raw = _str(key, default)
    p = Path(raw)
    if not p.is_absolute():
        p = BASE_DIR / p
    return p.resolve()


def _csv(key: str, default: str) -> list[str]:
    return [item.strip().lower() for item in _str(key, default).split(",") if item.strip()]


def _path_list(key: str) -> list[str]:
    """Semicolon/comma-separated list of paths; empty value means none."""
    raw = _str(key, "")
    if not raw:
        return []
    parts = re.split(r"[;,]", raw)
    return [p.strip() for p in parts if p.strip()]


def _resolution(key: str, default: str) -> tuple[int, int]:
    raw = _str(key, default).lower().replace("×", "x")
    try:
        w, h = raw.split("x")
        return max(16, int(w)), max(16, int(h))
    except ValueError:
        w, h = default.split("x")
        return int(w), int(h)


@dataclass
class Settings:
    # Application
    app_name: str = field(default_factory=lambda: _str("APP_NAME", "Radica - WanVideoGenerator"))
    app_version: str = field(default_factory=lambda: _str("APP_VERSION", "1.0.0"))
    app_env: str = field(default_factory=lambda: _str("APP_ENV", "development"))
    app_debug: bool = field(default_factory=lambda: _bool("APP_DEBUG", True))
    app_host: str = field(default_factory=lambda: _str("APP_HOST", "127.0.0.1"))
    app_port: int = field(default_factory=lambda: _int("APP_PORT", 8000))

    # Branding
    credits_concept: str = field(default_factory=lambda: _str("APP_CREDITS_CONCEPT", "Fabrizio Radica"))
    credits_project: str = field(default_factory=lambda: _str("APP_CREDITS_PROJECT", "RadicaDesign"))

    # Paths
    projects_root: Path = field(default_factory=lambda: _path("PROJECTS_ROOT", "./projects"))
    outputs_root: Path = field(default_factory=lambda: _path("OUTPUTS_ROOT", "./outputs"))
    temp_root: Path = field(default_factory=lambda: _path("TEMP_ROOT", "./temp"))
    models_root: Path = field(default_factory=lambda: _path("MODELS_ROOT", "./models"))
    workflows_root: Path = field(default_factory=lambda: _path("WORKFLOWS_ROOT", "./workflows"))
    logs_root: Path = field(default_factory=lambda: _path("LOGS_ROOT", "./logs"))
    static_root: Path = field(default_factory=lambda: _path("STATIC_ROOT", "./app/static"))

    # Backend — "wan" (real Wan 2.2+ inference, production default) or
    # "mock" (developer-only simulation, requires APP_ENV=development)
    generation_backend: str = field(default_factory=lambda: _str("GENERATION_BACKEND", "wan").lower())

    # Real Wan backend runtime options
    default_device: str = field(default_factory=lambda: _str("DEFAULT_DEVICE", "cuda"))
    wan_torch_dtype: str = field(default_factory=lambda: _str("WAN_TORCH_DTYPE", "float16"))
    # Offload mode: model (default, fits big models on consumer GPUs),
    # sequential (lowest VRAM, slower), none (whole pipeline on GPU)
    wan_offload_mode: str = field(default_factory=lambda: _str("WAN_OFFLOAD_MODE", "model"))
    wan_attention_slicing: bool = field(default_factory=lambda: _bool("WAN_ATTENTION_SLICING", False))
    # Flow-matching shift for the UniPC scheduler; <=0 = auto (5.0 for >=720p, 3.0 below)
    wan_flow_shift: float = field(default_factory=lambda: _float("WAN_FLOW_SHIFT", 0.0))
    # Dual-expert boundary for Wan2.2 A14B bundles with a transformer_2 component
    wan_boundary_ratio: float = field(default_factory=lambda: _float("WAN_BOUNDARY_RATIO", 0.875))
    # Allow fetching the small UMT5 tokenizer files from the Hugging Face Hub
    # when the bundle has no local tokenizer_path (cached after first download)
    wan_allow_hf_download: bool = field(default_factory=lambda: _bool("WAN_ALLOW_HF_DOWNLOAD", True))

    # --- Explicit device placement (patch6 §6) -------------------------------
    # Per-component device policy: cuda | cpu | auto. "auto" follows the offload
    # policy (components stream to GPU when needed). These are reported in the
    # effective device_map so the user always sees where each component ran.
    wan_main_device: str = field(default_factory=lambda: _str("WAN_MAIN_DEVICE", "").lower())
    wan_diffusion_device: str = field(default_factory=lambda: _str("WAN_DIFFUSION_DEVICE", "auto").lower())
    wan_text_encoder_device: str = field(default_factory=lambda: _str("WAN_TEXT_ENCODER_DEVICE", "auto").lower())
    wan_vae_device: str = field(default_factory=lambda: _str("WAN_VAE_DEVICE", "auto").lower())
    wan_vision_encoder_device: str = field(default_factory=lambda: _str("WAN_VISION_ENCODER_DEVICE", "auto").lower())
    # Offload policy: disabled | balanced | aggressive. Preferred over the legacy
    # WAN_OFFLOAD_MODE (model/sequential/none); when unset, derived from it.
    wan_offload_policy: str = field(default_factory=lambda: _str("WAN_OFFLOAD_POLICY", "").lower())
    wan_force_gpu_when_possible: bool = field(default_factory=lambda: _bool("WAN_FORCE_GPU_WHEN_POSSIBLE", True))
    wan_warn_if_text_encoder_on_cpu: bool = field(default_factory=lambda: _bool("WAN_WARN_IF_TEXT_ENCODER_ON_CPU", True))
    wan_warn_if_vae_on_cpu: bool = field(default_factory=lambda: _bool("WAN_WARN_IF_VAE_ON_CPU", True))

    # --- Explicit precision / dtype policy (patch6 §7) -----------------------
    wan_default_precision: str = field(default_factory=lambda: _str("WAN_DEFAULT_PRECISION", "fp16").lower())
    wan_allow_fp8: bool = field(default_factory=lambda: _bool("WAN_ALLOW_FP8", True))
    wan_allow_bf16: bool = field(default_factory=lambda: _bool("WAN_ALLOW_BF16", True))
    wan_allow_fp16: bool = field(default_factory=lambda: _bool("WAN_ALLOW_FP16", True))
    wan_warn_on_dtype_fallback: bool = field(default_factory=lambda: _bool("WAN_WARN_ON_DTYPE_FALLBACK", True))

    # --- VRAM cleanup / model warmth (patch6 §16, patch8 §19) ----------------
    wan_keep_model_warm: bool = field(default_factory=lambda: _bool("WAN_KEEP_MODEL_WARM", True))
    wan_clear_temp_vram_after_generation: bool = field(default_factory=lambda: _bool("WAN_CLEAR_TEMP_VRAM_AFTER_GENERATION", True))
    wan_unload_model_after_generation: bool = field(default_factory=lambda: _bool("WAN_UNLOAD_MODEL_AFTER_GENERATION", False))
    # patch8: cleanup after Stop/Cancel + logging of before/after memory.
    wan_clear_temp_vram_after_cancel: bool = field(default_factory=lambda: _bool("WAN_CLEAR_TEMP_VRAM_AFTER_CANCEL", True))
    wan_unload_model_after_cancel: bool = field(default_factory=lambda: _bool("WAN_UNLOAD_MODEL_AFTER_CANCEL", True))
    wan_log_vram_cleanup: bool = field(default_factory=lambda: _bool("WAN_LOG_VRAM_CLEANUP", True))

    # --- Wan2.2 TI2V 5B presets (patch8 §3-§6, §25) --------------------------
    wan_default_preset: str = field(default_factory=lambda: _str("WAN_DEFAULT_PRESET", "manual").lower())
    wan_preset_show_changes: bool = field(default_factory=lambda: _bool("WAN_PRESET_SHOW_CHANGES", True))
    wan_validate_resolution_multiple_of: int = field(default_factory=lambda: _int("WAN_VALIDATE_RESOLUTION_MULTIPLE_OF", 16))
    wan_default_fps: int = field(default_factory=lambda: _int("WAN_DEFAULT_FPS", 16))
    wan_fast_preview_frames: int = field(default_factory=lambda: _int("WAN_FAST_PREVIEW_FRAMES", 49))
    wan_fast_preview_steps: int = field(default_factory=lambda: _int("WAN_FAST_PREVIEW_STEPS", 18))
    wan_safe_quality_frames: int = field(default_factory=lambda: _int("WAN_SAFE_QUALITY_FRAMES", 81))
    wan_safe_quality_steps: int = field(default_factory=lambda: _int("WAN_SAFE_QUALITY_STEPS", 32))
    wan_low_vram_frames: int = field(default_factory=lambda: _int("WAN_LOW_VRAM_FRAMES", 49))
    wan_low_vram_steps: int = field(default_factory=lambda: _int("WAN_LOW_VRAM_STEPS", 22))

    # --- ComfyUI API render backend (patch6 §17, optional) -------------------
    comfyui_api_enabled: bool = field(default_factory=lambda: _bool("COMFYUI_API_ENABLED", False))
    comfyui_api_url: str = field(default_factory=lambda: _str("COMFYUI_API_URL", "http://127.0.0.1:8188"))
    comfyui_api_timeout_seconds: int = field(default_factory=lambda: _int("COMFYUI_API_TIMEOUT_SECONDS", 3600))

    # --- Sampler fallback policy (patch6c §8) --------------------------------
    # What to do when the requested sampler is not supported by the direct
    # backend: block | ask | route_to_comfyui | allow_with_warning. The default
    # is `block` — silent fallback to another sampler is NEVER the default.
    wan_sampler_fallback_policy: str = field(default_factory=lambda: _str("WAN_SAMPLER_FALLBACK_POLICY", "block").lower())
    wan_auto_route_unsupported_sampler_to_comfyui: bool = field(default_factory=lambda: _bool("WAN_AUTO_ROUTE_UNSUPPORTED_SAMPLER_TO_COMFYUI", True))
    wan_warn_on_sampler_fallback: bool = field(default_factory=lambda: _bool("WAN_WARN_ON_SAMPLER_FALLBACK", True))

    # --- ModelSamplingSD3 modifier (patch7 §17) ------------------------------
    # SD3 model sampling == the flow-matching sigma shift the ComfyUI
    # ModelSamplingSD3 node applies. The direct backend applies it exactly by
    # setting the scheduler shift; these control the default for NEW projects.
    wan_model_sampling_sd3_enabled_default: bool = field(default_factory=lambda: _bool("WAN_MODEL_SAMPLING_SD3_ENABLED_DEFAULT", True))
    wan_model_sampling_sd3_shift_default: float = field(default_factory=lambda: _float("WAN_MODEL_SAMPLING_SD3_SHIFT_DEFAULT", 8.0))
    # What to do if a render backend cannot apply ModelSamplingSD3:
    # block | ask | route_to_comfyui (never a silent-ignore default).
    wan_model_sampling_fallback_policy: str = field(default_factory=lambda: _str("WAN_MODEL_SAMPLING_FALLBACK_POLICY", "route_to_comfyui").lower())
    wan_auto_route_modelsamplingsd3_to_comfyui: bool = field(default_factory=lambda: _bool("WAN_AUTO_ROUTE_MODELSAMPLINGSD3_TO_COMFYUI", True))
    wan_warn_on_model_sampling_fallback: bool = field(default_factory=lambda: _bool("WAN_WARN_ON_MODEL_SAMPLING_FALLBACK", True))

    # Defaults
    default_generation_mode: str = field(default_factory=lambda: _str("DEFAULT_GENERATION_MODE", "text2video"))
    default_t2v_model: str = field(default_factory=lambda: _str("DEFAULT_T2V_MODEL", "wan2.2_t2v_14b"))
    default_i2v_model: str = field(default_factory=lambda: _str("DEFAULT_I2V_MODEL", "wan2.2_i2v_14b"))
    # Default model bundle component paths (empty = not configured)
    t2v_component_defaults: dict = field(default_factory=lambda: {
        "diffusion_model_path": _str("DEFAULT_T2V_DIFFUSION_MODEL_PATH", ""),
        "checkpoint_path": _str("DEFAULT_T2V_CHECKPOINT_PATH", ""),
        "vae_path": _str("DEFAULT_T2V_VAE_PATH", ""),
        "text_encoder_path": _str("DEFAULT_T2V_TEXT_ENCODER_PATH", ""),
        "clip_path": _str("DEFAULT_T2V_CLIP_PATH", ""),
        "t5_encoder_path": _str("DEFAULT_T2V_T5_ENCODER_PATH", ""),
        "tokenizer_path": _str("DEFAULT_T2V_TOKENIZER_PATH", ""),
        "config_path": _str("DEFAULT_T2V_CONFIG_PATH", ""),
        "scheduler_config_path": _str("DEFAULT_T2V_SCHEDULER_CONFIG_PATH", ""),
    })
    i2v_component_defaults: dict = field(default_factory=lambda: {
        "diffusion_model_path": _str("DEFAULT_I2V_DIFFUSION_MODEL_PATH", ""),
        "checkpoint_path": _str("DEFAULT_I2V_CHECKPOINT_PATH", ""),
        "vae_path": _str("DEFAULT_I2V_VAE_PATH", ""),
        "text_encoder_path": _str("DEFAULT_I2V_TEXT_ENCODER_PATH", ""),
        "clip_path": _str("DEFAULT_I2V_CLIP_PATH", ""),
        "t5_encoder_path": _str("DEFAULT_I2V_T5_ENCODER_PATH", ""),
        "vision_encoder_path": _str("DEFAULT_I2V_VISION_ENCODER_PATH", ""),
        "tokenizer_path": _str("DEFAULT_I2V_TOKENIZER_PATH", ""),
        "config_path": _str("DEFAULT_I2V_CONFIG_PATH", ""),
        "scheduler_config_path": _str("DEFAULT_I2V_SCHEDULER_CONFIG_PATH", ""),
    })
    default_lora_paths: list[str] = field(default_factory=lambda: _path_list("DEFAULT_LORA_PATHS"))
    default_control_model_paths: list[str] = field(default_factory=lambda: _path_list("DEFAULT_CONTROL_MODEL_PATHS"))
    default_upscaler_model_path: str = field(default_factory=lambda: _str("DEFAULT_UPSCALER_MODEL_PATH", ""))
    default_auxiliary_model_paths: list[str] = field(default_factory=lambda: _path_list("DEFAULT_AUXILIARY_MODEL_PATHS"))

    default_orientation: str = field(default_factory=lambda: _str("DEFAULT_ORIENTATION", "landscape"))
    default_resolution: tuple[int, int] = field(default_factory=lambda: _resolution("DEFAULT_RESOLUTION", "1280x704"))
    default_fps: int = field(default_factory=lambda: _int("DEFAULT_FPS", 24))
    default_frames: int = field(default_factory=lambda: _int("DEFAULT_FRAMES", 81))
    default_seed: int = field(default_factory=lambda: _int("DEFAULT_SEED", -1))
    default_guidance_scale: float = field(default_factory=lambda: _float("DEFAULT_GUIDANCE_SCALE", 6.0))
    default_motion_strength: float = field(default_factory=lambda: _float("DEFAULT_MOTION_STRENGTH", 0.7))
    default_steps: int = field(default_factory=lambda: _int("DEFAULT_STEPS", 30))
    default_output_format: str = field(default_factory=lambda: _str("DEFAULT_OUTPUT_FORMAT", "mp4"))

    # ComfyUI/KSampler compatibility defaults
    default_control_after_generate: str = field(default_factory=lambda: _str("DEFAULT_CONTROL_AFTER_GENERATE", "fixed").lower())
    default_sampler_name: str = field(default_factory=lambda: _str("DEFAULT_SAMPLER_NAME", "euler").lower())
    default_scheduler: str = field(default_factory=lambda: _str("DEFAULT_SCHEDULER", "simple").lower())
    default_denoise: float = field(default_factory=lambda: _float("DEFAULT_DENOISE", 1.0))

    # Test presets
    test_low_landscape: tuple[int, int] = field(default_factory=lambda: _resolution("TEST_LOW_LANDSCAPE_RESOLUTION", "384x224"))
    test_low_portrait: tuple[int, int] = field(default_factory=lambda: _resolution("TEST_LOW_PORTRAIT_RESOLUTION", "224x384"))
    test_quick_landscape: tuple[int, int] = field(default_factory=lambda: _resolution("TEST_QUICK_LANDSCAPE_RESOLUTION", "640x352"))
    test_quick_portrait: tuple[int, int] = field(default_factory=lambda: _resolution("TEST_QUICK_PORTRAIT_RESOLUTION", "352x640"))

    # Uploads
    max_upload_size_mb: int = field(default_factory=lambda: _int("MAX_UPLOAD_SIZE_MB", 50))
    allowed_image_extensions: list[str] = field(default_factory=lambda: _csv("ALLOWED_IMAGE_EXTENSIONS", "jpg,jpeg,png,webp"))
    allowed_video_extensions: list[str] = field(default_factory=lambda: _csv("ALLOWED_VIDEO_EXTENSIONS", "mp4,webm,mov"))

    # Video effects / Color & Look post-processing
    video_effects_enabled: bool = field(default_factory=lambda: _bool("VIDEO_EFFECTS_ENABLED", True))
    video_effects_preview_enabled: bool = field(default_factory=lambda: _bool("VIDEO_EFFECTS_PREVIEW_ENABLED", True))
    video_effects_preview_max_width: int = field(default_factory=lambda: _int("VIDEO_EFFECTS_PREVIEW_MAX_WIDTH", 640))

    # Audio tracks post-processing
    audio_tracks_enabled: bool = field(default_factory=lambda: _bool("AUDIO_TRACKS_ENABLED", True))
    allowed_audio_extensions: list[str] = field(default_factory=lambda: _csv("ALLOWED_AUDIO_EXTENSIONS", "mp3,wav,ogg,flac,m4a"))
    max_audio_upload_size_mb: int = field(default_factory=lambda: _int("MAX_AUDIO_UPLOAD_SIZE_MB", 100))
    ffmpeg_path: str = field(default_factory=lambda: _str("FFMPEG_PATH", "ffmpeg"))
    audio_default_volume: float = field(default_factory=lambda: _float("AUDIO_DEFAULT_VOLUME", 1.0))
    audio_default_fade_in: float = field(default_factory=lambda: _float("AUDIO_DEFAULT_FADE_IN", 0.0))
    audio_default_fade_out: float = field(default_factory=lambda: _float("AUDIO_DEFAULT_FADE_OUT", 0.0))
    audio_default_loop: bool = field(default_factory=lambda: _bool("AUDIO_DEFAULT_LOOP", False))
    audio_default_trim_to_video: bool = field(default_factory=lambda: _bool("AUDIO_DEFAULT_TRIM_TO_VIDEO", True))

    # Performance
    use_cuda: bool = field(default_factory=lambda: _bool("USE_CUDA", True))
    vram_warning_gb: int = field(default_factory=lambda: _int("VRAM_WARNING_GB", 8))
    enable_memory_optimization: bool = field(default_factory=lambda: _bool("ENABLE_MEMORY_OPTIMIZATION", True))
    max_parallel_jobs: int = field(default_factory=lambda: _int("MAX_PARALLEL_JOBS", 1))

    # System monitor
    system_monitor_enabled: bool = field(default_factory=lambda: _bool("SYSTEM_MONITOR_ENABLED", True))
    system_monitor_poll_interval_ms: int = field(default_factory=lambda: _int("SYSTEM_MONITOR_POLL_INTERVAL_MS", 2000))
    system_monitor_disk_path: Path = field(default_factory=lambda: _path("SYSTEM_MONITOR_DISK_PATH", "./projects"))
    system_monitor_show_gpu: bool = field(default_factory=lambda: _bool("SYSTEM_MONITOR_SHOW_GPU", True))
    system_monitor_show_disk: bool = field(default_factory=lambda: _bool("SYSTEM_MONITOR_SHOW_DISK", True))
    system_monitor_disk_warning_percent: int = field(default_factory=lambda: _int("SYSTEM_MONITOR_DISK_WARNING_PERCENT", 75))
    system_monitor_disk_critical_percent: int = field(default_factory=lambda: _int("SYSTEM_MONITOR_DISK_CRITICAL_PERCENT", 90))

    # Generation progress polling (global progress strip)
    generation_progress_poll_interval_ms: int = field(default_factory=lambda: _int("GENERATION_PROGRESS_POLL_INTERVAL_MS", 1000))
    generation_completed_visible_seconds: int = field(default_factory=lambda: _int("GENERATION_COMPLETED_VISIBLE_SECONDS", 30))
    generation_failed_visible_seconds: int = field(default_factory=lambda: _int("GENERATION_FAILED_VISIBLE_SECONDS", 60))

    # ComfyUI
    comfyui_export_enabled: bool = field(default_factory=lambda: _bool("COMFYUI_EXPORT_ENABLED", True))
    comfyui_workflow_version: str = field(default_factory=lambda: _str("COMFYUI_WORKFLOW_VERSION", "wan_video_generator_v1"))
    comfyui_default_save_node: bool = field(default_factory=lambda: _bool("COMFYUI_DEFAULT_SAVE_NODE", True))

    # Logging
    log_level: str = field(default_factory=lambda: _str("LOG_LEVEL", "INFO").upper())
    log_file: Path = field(default_factory=lambda: _path("LOG_FILE", "./logs/app.log"))

    # Security
    session_secret_key: str = field(default_factory=lambda: _str("SESSION_SECRET_KEY", "change_this_secret_key"))

    env_file_found: bool = field(default_factory=lambda: bool(_env_loaded))

    @property
    def credits_line(self) -> str:
        return f"Concept & Design: {self.credits_concept} — Project by {self.credits_project}"

    # Mapping between the legacy WAN_OFFLOAD_MODE (model/sequential/none) and the
    # patch6 WAN_OFFLOAD_POLICY (balanced/aggressive/disabled). Both describe the
    # same thing; the policy name is preferred when set.
    _OFFLOAD_POLICY_TO_MODE = {"balanced": "model", "aggressive": "sequential", "disabled": "none"}
    _OFFLOAD_MODE_TO_POLICY = {"model": "balanced", "auto": "balanced", "": "balanced",
                               "sequential": "aggressive", "none": "disabled"}

    def effective_offload_policy(self) -> str:
        """The active offload policy name (disabled | balanced | aggressive)."""
        if self.wan_offload_policy in self._OFFLOAD_POLICY_TO_MODE:
            return self.wan_offload_policy
        return self._OFFLOAD_MODE_TO_POLICY.get(self.wan_offload_mode.lower(), "balanced")

    def effective_sampler_fallback_policy(self) -> str:
        """The active sampler fallback policy (patch6c §8). When the policy is
        `route_to_comfyui` but auto-routing is disabled, it degrades to `block`
        so nothing is rendered with the wrong sampler."""
        policy = self.wan_sampler_fallback_policy.strip().lower()
        valid = ("block", "ask", "route_to_comfyui", "allow_with_warning")
        if policy not in valid:
            policy = "block"
        if policy == "route_to_comfyui" and not self.wan_auto_route_unsupported_sampler_to_comfyui:
            return "block"
        return policy

    def effective_model_sampling_fallback_policy(self) -> str:
        """The active ModelSamplingSD3 fallback policy (patch7 §16). Degrades
        route_to_comfyui to block when auto-routing is disabled, so nothing is
        rendered without the modifier when it was requested."""
        policy = self.wan_model_sampling_fallback_policy.strip().lower()
        if policy not in ("block", "ask", "route_to_comfyui"):
            policy = "route_to_comfyui"
        if policy == "route_to_comfyui" and not self.wan_auto_route_modelsamplingsd3_to_comfyui:
            return "block"
        return policy

    def effective_offload_mode(self) -> str:
        """The diffusers offload mode (model | sequential | none) to apply,
        derived from WAN_OFFLOAD_POLICY when set, else WAN_OFFLOAD_MODE."""
        if self.wan_offload_policy in self._OFFLOAD_POLICY_TO_MODE:
            return self._OFFLOAD_POLICY_TO_MODE[self.wan_offload_policy]
        return self.wan_offload_mode.lower() or "model"

    def ensure_directories(self) -> None:
        for directory in (
            self.projects_root,
            self.outputs_root,
            self.temp_root,
            self.models_root,
            self.workflows_root,
            self.logs_root,
        ):
            directory.mkdir(parents=True, exist_ok=True)


settings = Settings()


def setup_logging() -> logging.Logger:
    settings.log_file.parent.mkdir(parents=True, exist_ok=True)
    level = getattr(logging, settings.log_level, logging.INFO)
    logger = logging.getLogger("wanvideogen")
    if logger.handlers:
        return logger
    logger.setLevel(level)
    formatter = logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
    file_handler = logging.FileHandler(settings.log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    logger.propagate = False
    return logger


logger = setup_logging()

if not settings.env_file_found:
    logger.warning(".env file not found at %s — using built-in defaults. Copy .env.example to .env.", ENV_FILE)
