"""Model Manager page and API."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.routes.shared import page_context, templates
from app.services import model_service
from app.services.model_service import ModelError

router = APIRouter()


@router.get("/models", response_class=HTMLResponse)
async def models_page(request: Request):
    try:
        models = model_service.list_models()
    except ModelError:
        models = []
    return templates.TemplateResponse(request, "models.html", page_context(request, "models", models=[m.model_dump(mode="json") for m in models])
    )


@router.get("/api/models")
async def api_list_models():
    try:
        return [m.model_dump(mode="json") for m in model_service.list_models()]
    except ModelError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/api/models")
async def api_add_model(payload: dict):
    try:
        return model_service.add_model(payload).model_dump(mode="json")
    except (ModelError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.put("/api/models/{model_id}")
async def api_update_model(model_id: str, payload: dict):
    try:
        return model_service.update_model(model_id, payload).model_dump(mode="json")
    except (ModelError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/api/models/{model_id}")
async def api_remove_model(model_id: str):
    try:
        model_service.remove_model(model_id)
    except ModelError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"ok": True, "message": f"Model '{model_id}' removed."}


@router.post("/api/models/{model_id}/validate")
async def api_validate_model(model_id: str):
    try:
        return model_service.validate_model(model_id).model_dump(mode="json")
    except ModelError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/api/models/{model_id}/set-default")
async def api_set_default(model_id: str, payload: dict):
    try:
        model = model_service.set_default(model_id, payload.get("mode", ""))
    except ModelError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "message": f"'{model.display_name}' is now the default {payload.get('mode')} model."}
