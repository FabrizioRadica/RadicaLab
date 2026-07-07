"""Expose a safe, read-only view of the .env configuration for the UI."""

from __future__ import annotations

from app.config import settings


def safe_settings() -> dict:
    """Values shown in the Settings pages. Paths are shown for transparency
    (local tool), but secrets are never exposed."""
    return {
        "general": {
            "app_name": settings.app_name,
            "app_version": settings.app_version,
            "environment": settings.app_env,
            "debug": settings.app_debug,
            "default_generation_mode": settings.default_generation_mode,
            "default_output_format": settings.default_output_format,
            "generation_backend": settings.generation_backend,
        },
        "models": {
            "default_t2v_model": settings.default_t2v_model,
            "default_i2v_model": settings.default_i2v_model,
        },
        "generation_defaults": {
            "orientation": settings.default_orientation,
            "resolution": f"{settings.default_resolution[0]}x{settings.default_resolution[1]}",
            "fps": settings.default_fps,
            "frames": settings.default_frames,
            "seed": settings.default_seed,
            "guidance_scale": settings.default_guidance_scale,
            "motion_strength": settings.default_motion_strength,
            "steps": settings.default_steps,
        },
        "performance": {
            "use_cuda": settings.use_cuda,
            "default_device": settings.default_device,
            "vram_warning_gb": settings.vram_warning_gb,
            "memory_optimization": settings.enable_memory_optimization,
            "max_parallel_jobs": settings.max_parallel_jobs,
        },
        "wan_backend": {
            "torch_dtype": settings.wan_torch_dtype,
            "compute_precision": settings.wan_default_precision,
            "offload_mode": settings.effective_offload_mode(),
            "offload_policy": settings.effective_offload_policy(),
            "attention_slicing": settings.wan_attention_slicing,
            "flow_shift": settings.wan_flow_shift or "auto",
            "boundary_ratio": settings.wan_boundary_ratio,
            "allow_hf_download": settings.wan_allow_hf_download,
            "keep_model_warm": settings.wan_keep_model_warm,
            "clear_temp_vram_after_generation": settings.wan_clear_temp_vram_after_generation,
            "unload_model_after_generation": settings.wan_unload_model_after_generation,
        },
        "device_placement": {
            "main_device": settings.wan_main_device or settings.default_device,
            "diffusion_device": settings.wan_diffusion_device,
            "text_encoder_device": settings.wan_text_encoder_device,
            "vae_device": settings.wan_vae_device,
            "vision_encoder_device": settings.wan_vision_encoder_device,
            "force_gpu_when_possible": settings.wan_force_gpu_when_possible,
        },
        "paths": {
            "projects_root": str(settings.projects_root),
            "outputs_root": str(settings.outputs_root),
            "models_root": str(settings.models_root),
            "workflows_root": str(settings.workflows_root),
            "temp_root": str(settings.temp_root),
            "logs_root": str(settings.logs_root),
        },
        "uploads": {
            "max_upload_size_mb": settings.max_upload_size_mb,
            "allowed_image_extensions": settings.allowed_image_extensions,
            "allowed_video_extensions": settings.allowed_video_extensions,
        },
        "comfyui": {
            "export_enabled": settings.comfyui_export_enabled,
            "workflow_version": settings.comfyui_workflow_version,
            "default_save_node": settings.comfyui_default_save_node,
        },
        "logging": {
            "level": settings.log_level,
            "file": str(settings.log_file),
        },
        "env_file_found": settings.env_file_found,
        "credits": {
            "concept_and_design": settings.credits_concept,
            "project_by": settings.credits_project,
        },
    }


def read_log_tail(lines: int = 200) -> list[str]:
    if not settings.log_file.exists():
        return []
    try:
        content = settings.log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        return content[-lines:]
    except OSError:
        return []
