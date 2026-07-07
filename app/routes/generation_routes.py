"""Generation job API: submit jobs, poll status, camera motion helpers."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request

from app.config import logger, settings
from app.models.camera_motion_models import CameraMotionSettings
from app.services import camera_motion_service, project_service, wan_backend
from app.services.generation_service import (
    GenerationError,
    get_backend,
    job_manager,
    model_sampling_capabilities,
    wan_sampling_capabilities,
)
from app.services.project_service import ProjectError

router = APIRouter()


@router.get("/api/backend/status")
async def api_backend_status():
    """Readiness of the active generation backend (dependencies, device)."""
    try:
        backend = get_backend()
    except GenerationError as exc:
        return {"backend": settings.generation_backend, "is_mock": False,
                "ready": False, "error": str(exc)}
    if backend.is_mock:
        return {"backend": backend.name, "is_mock": True, "ready": True,
                "warning": "Developer-only mock backend — output is simulated, "
                           "not real Wan generation."}
    return {"is_mock": False, **wan_backend.backend_status(),
            # Real sampler capability (direct backend vs ComfyUI) + active
            # fallback policy; the UI shows honest per-backend support (patch6c §9).
            "sampling_support": wan_sampling_capabilities(),
            # ModelSamplingSD3 capability + defaults (patch7 §13).
            "model_sampling_support": model_sampling_capabilities()}


@router.get("/api/presets")
async def api_presets(project_id: str | None = None, model_id: str | None = None):
    """Wan2.2 5B preset catalogue + a non-restrictive recommendation based on
    detected VRAM and the detected model speed profile (patch8 §3/§12 +
    patchoptimization §2/§8).

    `model_id` overrides profile detection with a specific bundle — the UI passes
    the currently-selected (possibly unsaved) model so the grouping/recommendation
    reflect the live choice."""
    from app.services import model_service, preset_service

    vram = wan_backend.device_info().get("vram_total_gb")
    profile = None
    if model_id:
        try:
            bundle = model_service.get_model(model_id).component_snapshot()
            profile = preset_service.detect_wan_speed_profile(bundle)
        except model_service.ModelError:
            profile = None
    if profile is None and project_id:
        try:
            project = project_service.load_project(project_id)
            profile = preset_service.detect_profile_for_project(project)
        except ProjectError:
            profile = None
    return {
        "presets": preset_service.preset_definitions(),
        "default_preset": settings.wan_default_preset,
        "show_changes": settings.wan_preset_show_changes,
        "resolution_multiple_of": settings.wan_validate_resolution_multiple_of,
        "detected_model_profile": profile,
        "recommendation": preset_service.recommend_preset(vram, profile),
    }


@router.get("/api/models/{model_id}/speed-profile")
async def api_model_speed_profile(model_id: str):
    """Detected Wan speed profile (normal/turbo/lightning/lightx2v/fast) for a
    model bundle (patchoptimization §2/§8) — powers the UI 'detected profile' hint."""
    from app.services import model_service, preset_service

    try:
        bundle = model_service.get_model(model_id).component_snapshot()
    except model_service.ModelError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    profile = preset_service.detect_wan_speed_profile(bundle)
    return {"model_id": model_id, "profile": profile,
            "accelerated": profile in preset_service.ACCELERATED_PROFILES}


@router.post("/api/presets/preview")
async def api_preset_preview(request: Request):
    """Compute a preset's target values + change summary for a project WITHOUT
    saving anything (patch8 §3). The UI applies the values to the form and shows
    the summary so nothing is changed silently.

    Defensive parsing (patch8b §4): the primary fix is frontend-side (send a
    real JSON object), but a body that arrives as a stringified JSON — e.g. a
    caller that double-encodes — is parsed once here instead of returning the
    raw Pydantic "Input should be a valid dictionary" error."""
    from app.services import preset_service

    try:
        payload = await request.json()
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=422, detail="Preset payload must be a JSON object.")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            raise HTTPException(status_code=422, detail="Preset payload must be a JSON object.")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Preset payload must be a JSON object.")

    project_id = payload.get("project_id", "")
    preset_id = payload.get("preset_id", "manual")
    try:
        project = project_service.load_project(project_id)
    except ProjectError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return preset_service.preset_changes(project, preset_id)


@router.post("/api/generate")
async def api_generate(payload: dict):
    project_id = payload.get("project_id", "")
    # patch6c §8 — user confirmation for an `ask`-policy sampler fallback.
    confirm_fallback = bool(payload.get("confirm_sampler_fallback", False))
    try:
        project = project_service.load_project(project_id)
    except ProjectError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    try:
        job = job_manager.submit(project, confirm_fallback=confirm_fallback)
    except GenerationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return job.model_dump(mode="json")


@router.post("/api/backend/sampler-plan")
async def api_sampler_plan(payload: dict):
    """Preview the sampler/backend routing AND ModelSamplingSD3 resolution for a
    project's current settings without starting a render (patch6c §9/§10,
    patch7 §13) — powers the live UI status."""
    from app.services.generation_service import (
        resolve_model_sampling_plan,
        resolve_sampling_plan,
    )

    project_id = payload.get("project_id", "")
    try:
        project = project_service.load_project(project_id)
    except ProjectError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    plan = resolve_sampling_plan(project)
    data = plan.as_metadata()
    data["warnings"] = plan.warnings
    data["confirmation_message"] = plan.confirmation_message
    ms_plan = resolve_model_sampling_plan(project, plan)
    data["model_sampling"] = ms_plan.as_metadata()
    data["model_sampling"]["confirmation_message"] = ms_plan.confirmation_message
    return data


@router.get("/api/jobs/{job_id}")
async def api_job_status(job_id: str):
    job = job_manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
    return job.model_dump(mode="json")


@router.post("/api/generation/jobs/{job_id}/cancel")
async def api_cancel_job(job_id: str):
    """Request cancellation of a running/pending generation job (patch6b §5).
    Non-blocking: the worker performs the actual stop and cleanup."""
    try:
        return job_manager.request_cancel(job_id)
    except GenerationError as exc:
        # "not found" → 404; every other reason (already finished, etc.) → 409.
        status = 404 if "not found" in str(exc).lower() else 409
        raise HTTPException(status_code=status, detail=str(exc))


# Project-scoped alias for the same action (the persistent-progress API is
# project-scoped elsewhere).
@router.post("/api/projects/{project_id}/generation/jobs/{job_id}/cancel")
async def api_cancel_project_job(project_id: str, job_id: str):
    return await api_cancel_job(job_id)


@router.get("/api/jobs")
async def api_jobs(project_id: str | None = None):
    return [j.model_dump(mode="json") for j in job_manager.list(project_id)]


# --- Persistent-progress endpoints (patch3 §4.4) --------------------------
# Server-side job state survives page navigation; these let any page recover
# active and recent jobs on load.

@router.get("/api/generation/jobs")
async def api_generation_jobs():
    """All active and recent generation jobs (newest first)."""
    return [j.model_dump(mode="json") for j in job_manager.list()]


@router.get("/api/generation/jobs/{job_id}")
async def api_generation_job(job_id: str):
    return await api_job_status(job_id)


@router.get("/api/generation/active")
async def api_generation_active():
    """Running/pending jobs plus recently finished ones (for UI recovery)."""
    from datetime import datetime, timedelta, timezone

    jobs = job_manager.list()
    active = [j for j in jobs if j.status.value in ("pending", "running", "cancel_requested")]
    keep_completed = timedelta(seconds=max(settings.generation_completed_visible_seconds, 0))
    keep_failed = timedelta(seconds=max(settings.generation_failed_visible_seconds, 0))
    now = datetime.now(timezone.utc)
    recent = []
    for j in jobs:
        if j.status.value not in ("completed", "failed", "cancelled") or not j.finished_at:
            continue
        try:
            age = now - datetime.fromisoformat(j.finished_at)
        except ValueError:
            continue
        if age <= (keep_completed if j.status.value == "completed" else keep_failed):
            recent.append(j)
    return {
        "active": [j.model_dump(mode="json") for j in active],
        "recent": [j.model_dump(mode="json") for j in recent],
    }


@router.get("/api/projects/{project_id}/generation/jobs")
async def api_project_generation_jobs(project_id: str):
    return [j.model_dump(mode="json") for j in job_manager.list(project_id)]


@router.get("/api/system/stats")
async def api_system_stats():
    """Live CPU/RAM/GPU/VRAM/disk usage for the topbar monitor."""
    from app.services import system_monitor_service

    if not settings.system_monitor_enabled:
        return {"ok": False, "enabled": False, "stats": None}
    try:
        return {"ok": True, "enabled": True, "stats": system_monitor_service.get_system_stats()}
    except Exception as exc:  # noqa: BLE001 — stats must never break the app
        logger.warning("System stats collection failed: %s", exc)
        return {"ok": False, "enabled": True, "stats": None, "error": str(exc)}


@router.get("/api/camera-motion/options")
async def api_camera_motion_options():
    return camera_motion_service.get_options()


@router.post("/api/camera-motion/fragment")
async def api_camera_motion_fragment(payload: dict):
    try:
        cm = CameraMotionSettings.model_validate(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid camera motion settings: {exc}")
    return {"fragment": camera_motion_service.build_fragment(cm)}
