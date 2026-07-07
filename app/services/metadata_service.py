"""Per-video metadata files stored in the project metadata/ folder."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.config import logger, settings
from app.models.project_models import Project


def _requested_settings(project: Project, seed_used: int, bundle: dict) -> dict:
    """The settings the user asked for, before any preset/backend adjustment
    (patch6 §5). Mirrors the effective_settings shape for easy diffing."""
    from app.config import settings as _settings

    return {
        "mode": project.generation_mode.value,
        "model_bundle_id": bundle.get("model_bundle_id", project.model_id),
        "prompt": project.composed_prompt(),
        "negative_prompt": project.negative_prompt,
        "seed": seed_used,
        "steps": project.params.advanced.steps,
        "cfg": project.params.guidance_scale,
        "guidance_scale": project.params.guidance_scale,
        "sampler_name": project.params.sampler_name,
        "scheduler": project.params.scheduler,
        "denoise": project.params.denoise,
        "resolution": project.resolution.label(),
        "frames": project.params.frames,
        "fps": project.params.fps,
        "precision": project.params.advanced.precision,
        "offload_policy": _settings.effective_offload_policy(),
        "quality_preset": getattr(project.params, "quality_preset", "none"),
        "model_sampling_sd3": bool(getattr(project.params, "model_sampling", None)
                                   and project.params.model_sampling.enabled),
        "model_sampling_shift": (project.params.model_sampling.shift
                                 if getattr(project.params, "model_sampling", None)
                                 and project.params.model_sampling.enabled else None),
    }


def _preset_block(project: Project) -> dict | None:
    """Wan2.2 5B preset requested-vs-effective block (patch8). None on failure."""
    try:
        from app.services import preset_service

        return preset_service.preset_metadata(project)
    except Exception as exc:  # noqa: BLE001 — metadata must never fail on this
        logger.warning("Could not build preset metadata block: %s", exc)
        return None


def _model_profile_block(project: Project, bundle: dict) -> dict | None:
    """Detected model profile / preset-profile / effective-shift block
    (patchoptimization §10). None on failure — metadata must never break."""
    try:
        from app.services import preset_service

        return preset_service.generation_profile_metadata(project, bundle or None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not build model profile metadata block: %s", exc)
        return None


def build_video_metadata(
    project: Project,
    output_filename: str,
    seed_used: int,
    backend: str,
    is_mock: bool,
    actual_format: str,
    duration_seconds: float | None = None,
    model_bundle: dict | None = None,
    unsupported_params: list[str] | None = None,
    report: dict | None = None,
    preset_notes: list[str] | None = None,
    sampler_backend: dict | None = None,
    model_sampling: dict | None = None,
    vram_cleanup: dict | None = None,
) -> dict:
    cm = project.camera_motion
    bundle = model_bundle or {}
    report = report or {}
    # Combine backend fallback warnings and sampling-support notes (deduplicated).
    warnings = list(report.get("warnings", []))
    for note in (unsupported_params or []):
        if note not in warnings:
            warnings.append(note)
    return {
        "application": settings.app_name,
        "app_version": settings.app_version,
        "credits": {
            "concept_and_design": settings.credits_concept,
            "project_by": settings.credits_project,
        },
        "project_id": project.id,
        "project_name": project.name,
        "generation_mode": project.generation_mode.value,
        "model_id": project.model_id,
        "model_name": bundle.get("model_bundle_name", project.model_id),
        "model_bundle_id": bundle.get("model_bundle_id", project.model_id),
        "model_bundle_name": bundle.get("model_bundle_name", ""),
        "model_bundle_snapshot": bundle or None,
        "diffusion_model_path": bundle.get("diffusion_model_path", ""),
        "checkpoint_path": bundle.get("checkpoint_path", ""),
        "vae_path": bundle.get("vae_path", ""),
        "text_encoder_path": bundle.get("text_encoder_path", ""),
        "clip_path": bundle.get("clip_path", ""),
        "t5_encoder_path": bundle.get("t5_encoder_path", ""),
        "vision_encoder_path": bundle.get("vision_encoder_path", ""),
        "tokenizer_path": bundle.get("tokenizer_path", ""),
        "config_path": bundle.get("config_path", ""),
        "scheduler_config_path": bundle.get("scheduler_config_path", ""),
        "lora_paths": bundle.get("lora_paths", []),
        "control_model_paths": bundle.get("control_model_paths", []),
        "upscaler_model_path": bundle.get("upscaler_model_path", ""),
        "auxiliary_model_paths": bundle.get("auxiliary_model_paths", []),
        "positive_prompt": project.positive_prompt,
        "negative_prompt": project.negative_prompt,
        "final_composed_prompt": project.composed_prompt(),
        "camera_motion": cm.model_dump(mode="json"),
        "seed": seed_used,
        "fps": project.params.fps,
        "frames": project.params.frames,
        "resolution": project.resolution.label(),
        "orientation": project.orientation.value,
        "guidance_scale": project.params.guidance_scale,
        # ComfyUI/KSampler-compatible sampling parameters, grouped for clarity.
        # guidance_scale and cfg are the same value under both naming schemes;
        # denoise is its own parameter (never CFG / motion / image influence).
        "sampling": {
            "seed": seed_used,
            "control_after_generate": project.params.control_after_generate,
            "steps": project.params.advanced.steps,
            "cfg": project.params.guidance_scale,
            "guidance_scale": project.params.guidance_scale,
            "sampler_name": project.params.sampler_name,
            "scheduler": project.params.scheduler,
            "denoise": project.params.denoise,
            # Parameters the active backend could not apply (empty = all applied).
            "unsupported_by_backend": unsupported_params or [],
        },
        # --- Requested vs effective sampler/backend routing (patch6c §12) -----
        "sampler_backend": sampler_backend,
        # --- ModelSamplingSD3 requested/effective/applied (patch7 §14) --------
        "model_sampling": model_sampling,
        # --- VRAM cleanup report (patch8 §22) ---------------------------------
        "vram_cleanup": vram_cleanup,
        # --- Wan2.2 5B preset requested/effective (patch8 §22/§27) ------------
        "wan_preset": _preset_block(project),
        # --- Detected model speed profile + turbo warning (patchoptimization
        # §10). Flattened top-level too so downstream tools/tests can read them
        # directly. All fields are DERIVED — no user value is ever rewritten. ---
        "wan_model_profile": (_mp := _model_profile_block(project, bundle)),
        "detected_model_profile": (_mp or {}).get("detected_model_profile"),
        "selected_preset_id": (_mp or {}).get("selected_preset_id"),
        "selected_preset_profile": (_mp or {}).get("selected_preset_profile"),
        "model_sampling_shift_mode": (_mp or {}).get("model_sampling_shift_mode"),
        "effective_model_sampling_shift": (_mp or {}).get("effective_model_sampling_shift"),
        "turbo_parameter_warning": bool((_mp or {}).get("turbo_parameter_warning")),
        # --- Requested vs effective + backend diagnostics (patch6 §4/§5) ------
        "quality_preset": getattr(project.params, "quality_preset", "none"),
        "preset_notes": preset_notes or [],
        "requested_settings": _requested_settings(project, seed_used, bundle),
        "effective_settings": report.get("effective_settings"),
        "device_map": report.get("device_map"),
        "dtype_map": report.get("dtype_map"),
        "timings": report.get("timings"),
        "gpu_memory": report.get("gpu_memory"),
        "image_preprocessing": report.get("image_preprocessing"),
        "final_positive_prompt": report.get("final_positive_prompt", project.composed_prompt()),
        "final_negative_prompt": report.get("final_negative_prompt", project.negative_prompt),
        "warnings": warnings,
        "motion_strength": project.params.motion_strength,
        "image_influence": project.params.image_influence if project.generation_mode.value == "image2video" else None,
        "advanced_parameters": project.params.advanced.model_dump(mode="json"),
        "source_image": project.source_image,
        "output_file": output_filename,
        "output_format": actual_format,
        "duration_seconds": duration_seconds,
        "requested_duration_seconds": round(project.params.frames / max(project.params.fps, 1), 3),
        "actual_duration_seconds": duration_seconds,
        "backend": backend,
        "mock_generation": is_mock,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def write_cancellation_metadata(project_dir: Path, output_stem: str, info: dict) -> str:
    """Record a cancelled generation (patch6b §11). Written to the project
    metadata/ folder with a `.cancelled.json` suffix so it is never mistaken
    for a completed video's metadata."""
    from datetime import datetime, timezone

    meta_dir = project_dir / "metadata"
    meta_dir.mkdir(exist_ok=True)
    meta_name = f"{output_stem}.cancelled.json"
    payload = {
        "application": settings.app_name,
        "credits": {
            "concept_and_design": settings.credits_concept,
            "project_by": settings.credits_project,
        },
        "cancel_requested_at": info.get("cancel_requested_at"),
        "written_at": datetime.now(timezone.utc).isoformat(),
        **info,
    }
    (meta_dir / meta_name).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Cancellation metadata written: %s", meta_dir / meta_name)
    return meta_name


def write_video_metadata(project_dir: Path, output_filename: str, metadata: dict) -> str:
    meta_dir = project_dir / "metadata"
    meta_dir.mkdir(exist_ok=True)
    meta_name = Path(output_filename).stem + ".json"
    (meta_dir / meta_name).write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Video metadata written: %s", meta_dir / meta_name)
    return meta_name
