"""Workflows page and ComfyUI export API."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.routes.shared import page_context, templates
from app.services import project_service, workflow_export_service
from app.services.project_service import ProjectError
from app.services.workflow_export_service import WorkflowExportError

router = APIRouter()


def _collect_workflows() -> list[dict]:
    items: list[dict] = []
    for summary in project_service.list_projects():
        try:
            project = project_service.load_project(summary.id)
        except ProjectError:
            continue
        for wf in project.exported_workflows:
            items.append(
                {
                    "project_id": project.id,
                    "project_name": project.name,
                    "filename": wf.filename,
                    "path": wf.path,
                    "url": f"/media/projects/{project.id}/workflows/{wf.filename}",
                    "created_at": wf.created_at,
                }
            )
    items.sort(key=lambda w: w["created_at"], reverse=True)
    return items


@router.get("/workflows", response_class=HTMLResponse)
async def workflows_page(request: Request):
    return templates.TemplateResponse(request, "workflows.html", page_context(request, "workflows", workflows=_collect_workflows())
    )


@router.get("/api/workflows")
async def api_workflows():
    return _collect_workflows()


@router.post("/api/projects/{project_id}/export-workflow")
async def api_export_workflow(project_id: str):
    try:
        return workflow_export_service.export_workflow(project_id)
    except ProjectError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except WorkflowExportError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/api/projects/{project_id}/compare-comfyui")
async def api_compare_comfyui(project_id: str):
    """Parity comparison tool (patch6 §18): requested vs effective vs ComfyUI."""
    try:
        return workflow_export_service.compare_with_comfyui(project_id)
    except ProjectError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except WorkflowExportError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
