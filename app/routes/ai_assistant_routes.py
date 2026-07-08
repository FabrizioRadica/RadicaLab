"""AI Prompt Assistant API (patch §3-§9, §12).

Config CRUD, provider reachability test, single-clip prompt generation, sequence
planning, SequenceQueue population and resource release/status. The assistant
never renders — these routes only produce prompts/plans and hand them to the
existing SequenceQueue (patch §2.3).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.config import logger
from app.models.ai_assistant_models import PROVIDER_DEFAULTS
from app.services.ai_assistant import config_service, prompt_service
from app.services.ai_assistant import sequence_population_service as population
from app.services.ai_assistant.providers import AIProviderError, get_provider
from app.services.ai_assistant.resource_manager import STATUS_IDLE, resource_manager

router = APIRouter()


async def _json_body(request: Request) -> dict:
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 — body may be missing/invalid
        return {}
    return payload if isinstance(payload, dict) else {}


def _require_enabled() -> None:
    config = config_service.load_config()
    if not config.prompt_assistant.enabled:
        raise HTTPException(status_code=403, detail="The Prompt Assistant is disabled in Settings.")


# ==========================================================================
# Configuration (patch §3/§12)
# ==========================================================================
@router.get("/api/ai-assistant/config")
async def api_get_config():
    config = config_service.load_config()
    return {"config": config.safe_dict(), "provider_defaults": PROVIDER_DEFAULTS}


@router.put("/api/ai-assistant/config")
async def api_update_config(request: Request):
    body = await _json_body(request)
    try:
        updated = config_service.update_config(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"config": updated.safe_dict(), "provider_defaults": PROVIDER_DEFAULTS}


# ==========================================================================
# Provider reachability test (patch §13)
# ==========================================================================
@router.post("/api/ai-assistant/test")
async def api_test_provider():
    config = config_service.load_config()
    provider = get_provider(config.provider)
    try:
        text = await provider.generate_chat_completion([
            {"role": "system", "content": "You are a connectivity check."},
            {"role": "user", "content": "Reply with the single word: OK"},
        ])
    except AIProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        # Best-effort release so a test never leaves a local model resident.
        try:
            await resource_manager.release(provider, config.resources, config.provider.is_local())
        except Exception:  # noqa: BLE001
            pass
        if not config.provider.is_local():
            resource_manager.set_status(STATUS_IDLE)
    return {"ok": True, "provider": config.provider.provider.value,
            "model": config.provider.effective_model(),
            "sample": (text or "")[:200]}


# ==========================================================================
# Single Clip prompt generation (patch §6.3/§7.1)
# ==========================================================================
@router.post("/api/ai-assistant/single-clip")
async def api_single_clip(request: Request):
    _require_enabled()
    config = config_service.load_config()
    brief = await _json_body(request)
    warnings = resource_manager.preflight_warnings(config.resources, config.provider.is_local())
    try:
        result, release = await prompt_service.generate_single_clip(config, brief)
    except AIProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Single-clip prompt generation failed")
        raise HTTPException(status_code=500, detail=f"Prompt generation failed: {exc}")
    return {"result": result.model_dump(mode="json"), "release": release,
            "warnings": warnings, "status": resource_manager.status}


# ==========================================================================
# Sequence planning (patch §6.4/§7.2)
# ==========================================================================
@router.post("/api/ai-assistant/sequence-plan")
async def api_sequence_plan(request: Request):
    _require_enabled()
    config = config_service.load_config()
    brief = await _json_body(request)
    warnings = resource_manager.preflight_warnings(config.resources, config.provider.is_local())
    try:
        plan, release = await prompt_service.generate_sequence_plan(config, brief)
    except AIProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Sequence plan generation failed")
        raise HTTPException(status_code=500, detail=f"Sequence planning failed: {exc}")
    if not plan.parsed_json:
        # The response wasn't machine-readable — return it so the UI can show the
        # raw text and let the user retry/copy (patch §13).
        return {"plan": plan.model_dump(mode="json"), "release": release,
                "warnings": warnings, "parse_error": True, "status": resource_manager.status}
    return {"plan": plan.model_dump(mode="json"), "release": release,
            "warnings": warnings, "parse_error": False, "status": resource_manager.status}


# ==========================================================================
# SequenceQueue population (patch §9)
# ==========================================================================
@router.post("/api/ai-assistant/sequences/{sequence_id}/populate")
async def api_populate(sequence_id: str, request: Request):
    _require_enabled()
    body = await _json_body(request)
    from app.models.ai_assistant_models import SequencePlan
    try:
        plan = SequencePlan.model_validate(body.get("plan") or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid sequence plan: {exc}")
    mode = body.get("mode", "append")
    resource_manager.set_status("AI Assistant adding clips to SequenceQueue...")
    try:
        result = population.populate(sequence_id, plan, mode)
    except population.PopulationError as exc:
        resource_manager.set_status(STATUS_IDLE)
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        if resource_manager.status.startswith("AI Assistant adding"):
            resource_manager.set_status(STATUS_IDLE)
    return result


# ==========================================================================
# Resource release / status (patch §5.3/§14)
# ==========================================================================
@router.post("/api/ai-assistant/release")
async def api_release():
    config = config_service.load_config()
    provider = get_provider(config.provider)
    result = await resource_manager.release(provider, config.resources, config.provider.is_local())
    result["provider"] = config.provider.provider.value
    return result


@router.get("/api/ai-assistant/status")
async def api_status():
    return resource_manager.snapshot()
