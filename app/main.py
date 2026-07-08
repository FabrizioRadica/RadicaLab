"""
Radica - WanVideoGenerator — FastAPI application entry point.

Concept & Design: Fabrizio Radica — Project by RadicaDesign

Run with:  uvicorn app.main:app  (or: python -m app.main)
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import logger, settings
from app.routes import (
    ai_assistant_routes,
    audio_routes,
    video_effects_routes,
    generation_routes,
    home_routes,
    library_routes,
    model_routes,
    project_routes,
    sequence_routes,
    settings_routes,
    workflow_routes,
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings.ensure_directories()
    logger.info("%s v%s starting (%s) — %s", settings.app_name, settings.app_version,
                settings.app_env, settings.credits_line)
    logger.info("Configuration loaded (env file found: %s), backend: %s",
                settings.env_file_found, settings.generation_backend)
    yield
    logger.info("%s shutting down", settings.app_name)


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Browser-based AI video generation studio for Wan2.2+ — "
                f"{settings.credits_line}",
    lifespan=lifespan,
    docs_url="/api/docs" if settings.app_debug else None,
)

app.mount("/static", StaticFiles(directory=str(settings.static_root)), name="static")

app.include_router(home_routes.router)
app.include_router(project_routes.router)
app.include_router(generation_routes.router)
app.include_router(sequence_routes.router)
app.include_router(model_routes.router)
app.include_router(workflow_routes.router)
app.include_router(library_routes.router)
app.include_router(audio_routes.router)
app.include_router(video_effects_routes.router)
app.include_router(settings_routes.router)
app.include_router(ai_assistant_routes.router)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    detail = str(exc) if settings.app_debug else "An unexpected server error occurred. Check the application log."
    return JSONResponse(status_code=500, content={"detail": detail})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.app_host, port=settings.app_port, reload=settings.app_debug)
