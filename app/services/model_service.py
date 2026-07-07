"""Wan Model Bundle registry management.

The registry lives in <MODELS_ROOT>/registry.json. Every entry is a Model
Bundle: the group of component files a Wan pipeline may need (diffusion model,
checkpoint, VAE, encoders, tokenizer, configs, LoRAs…). Legacy single-path
entries are normalized on load so old registries keep working.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from app.config import logger, settings
from app.models.wan_model_registry import (
    CORE_COMPONENT_FIELDS,
    OPTIONAL_COMPONENT_FIELDS,
    STATUS_LABELS,
    ModelGenerationType,
    ModelStatus,
    ModelValidationResult,
    WanModel,
    normalize_legacy_entry,
    utc_now,
)

_REGISTRY_LOCK = threading.Lock()
_SAFE_ID_RE = re.compile(r"[^a-z0-9._\-]+")


class ModelError(Exception):
    """Raised for model registry failures with a user-readable message."""


def _registry_file() -> Path:
    return settings.models_root / "registry.json"


def _seed_models() -> list[WanModel]:
    """Initial registry entries. Paths point inside MODELS_ROOT and will show
    status 'missing' until the user downloads real checkpoints there."""
    root = settings.models_root
    return [
        WanModel(
            id="wan2.2_t2v_5b",
            display_name="Wan2.2 T2V 5B",
            generation_type=ModelGenerationType.TEXT2VIDEO,
            checkpoint_path=str(root / "Wan2.2_T2V_5B"),
            recommended_vram_gb=6,
            recommended_resolutions=["640x352", "1280x704"],
            notes="Lightweight model, good for fast tests.",
        ),
        WanModel(
            id="wan2.2_t2v_14b",
            display_name="Wan2.2 T2V 14B",
            generation_type=ModelGenerationType.TEXT2VIDEO,
            checkpoint_path=str(root / "Wan2.2_T2V_14B"),
            recommended_vram_gb=12,
            recommended_resolutions=["1280x704", "1920x1088"],
            notes="Best for complex scenes and camera movement.",
            default_t2v=True,
        ),
        WanModel(
            id="wan2.2_i2v_14b",
            display_name="Wan2.2 I2V 14B",
            generation_type=ModelGenerationType.IMAGE2VIDEO,
            checkpoint_path=str(root / "Wan2.2_I2V_14B"),
            recommended_vram_gb=14,
            recommended_resolutions=["1280x704", "704x1280"],
            notes="Image-to-video generation from a source frame.",
            default_i2v=True,
        ),
            WanModel(
            id="wan2.2_t2v_5B_fp8",
            display_name="Wan2.2 T2V 5B",
            generation_type=ModelGenerationType.TEXT2VIDEO,
            checkpoint_path=str(root / "Wan2.2_T2V_5B"),
            recommended_vram_gb=6,
            recommended_resolutions=["640x352", "1280x704"],
            notes="Very Lightweight model, good for fast tests.",
            default_t2v=True,
        ),
    ]


def _apply_env_components(model: WanModel, mode: str) -> None:
    """Copy DEFAULT_T2V_*/DEFAULT_I2V_* component paths from .env onto a bundle
    (only filling components that are still empty)."""
    comp = settings.t2v_component_defaults if mode == "text2video" else settings.i2v_component_defaults
    for field, value in comp.items():
        if value and not getattr(model, field, ""):
            setattr(model, field, value)
    if settings.default_lora_paths and not model.lora_paths:
        model.lora_paths = list(settings.default_lora_paths)
    if settings.default_control_model_paths and not model.control_model_paths:
        model.control_model_paths = list(settings.default_control_model_paths)
    if settings.default_upscaler_model_path and not model.upscaler_model_path:
        model.upscaler_model_path = settings.default_upscaler_model_path
    if settings.default_auxiliary_model_paths and not model.auxiliary_model_paths:
        model.auxiliary_model_paths = list(settings.default_auxiliary_model_paths)


def _ensure_env_defaults(models: list[WanModel]) -> bool:
    """Auto-register the .env default model ids if they are not in the registry,
    so a customized DEFAULT_T2V_MODEL/DEFAULT_I2V_MODEL works out of the box."""
    changed = False
    defaults = (
        (settings.default_t2v_model, "default_t2v", "text2video"),
        (settings.default_i2v_model, "default_i2v", "image2video"),
    )
    for model_id, flag, mode in defaults:
        if not model_id or any(m.id == model_id for m in models):
            continue
        entry = WanModel(
            id=model_id,
            display_name=model_id,
            family="Wan",
            generation_type=ModelGenerationType.BOTH,
            checkpoint_path=str(settings.models_root / model_id),
            notes="Auto-registered from .env default — edit this bundle and set the real component paths.",
        )
        _apply_env_components(entry, mode)
        setattr(entry, flag, True)
        entry.status = _compute_status(entry)
        models.append(entry)
        changed = True
        logger.info("Model bundle auto-registered from .env defaults: %s", model_id)
    return changed


def _load_registry() -> list[WanModel]:
    rfile = _registry_file()
    if not rfile.exists():
        models = _seed_models()
        _ensure_env_defaults(models)
        _save_registry(models)
        logger.info("Model registry seeded with %d entries at %s", len(models), rfile)
        return models
    try:
        data = json.loads(rfile.read_text(encoding="utf-8"))
        models = [WanModel.model_validate(normalize_legacy_entry(item)) for item in data.get("models", [])]
    except (json.JSONDecodeError, ValueError) as exc:
        logger.error("Model registry corrupted (%s): %s", rfile, exc)
        raise ModelError("The model registry file is corrupted. Fix or delete models/registry.json.") from exc
    if _ensure_env_defaults(models):
        _save_registry(models)
    return models


def _save_registry(models: list[WanModel]) -> None:
    rfile = _registry_file()
    rfile.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema": "wan_model_registry/2", "models": [m.model_dump(mode="json") for m in models]}
    tmp = rfile.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(rfile)


def list_models(refresh_status: bool = True) -> list[WanModel]:
    with _REGISTRY_LOCK:
        models = _load_registry()
        if refresh_status:
            changed = False
            for model in models:
                new_status = _compute_status(model)
                if new_status != model.status:
                    model.status = new_status
                    changed = True
            if changed:
                _save_registry(models)
        return models


def get_model(model_id: str) -> WanModel:
    for model in list_models(refresh_status=False):
        if model.id == model_id:
            return model
    raise ModelError(f"Model '{model_id}' not found in the registry.")


def _configured_paths(model: WanModel) -> list[tuple[str, str, str]]:
    """All configured (non-empty) component paths as (field, label, path)."""
    items: list[tuple[str, str, str]] = []
    for field, label in CORE_COMPONENT_FIELDS:
        value = getattr(model, field)
        if value:
            items.append((field, label, value))
    for field, label in OPTIONAL_COMPONENT_FIELDS:
        value = getattr(model, field)
        if isinstance(value, list):
            for i, path in enumerate(value):
                if path:
                    items.append((field, f"{label} #{i + 1}", path))
        elif value:
            items.append((field, label, value))
    for name, path in model.custom_component_paths.items():
        if path:
            items.append(("custom_component_paths", f"Custom: {name}", path))
    return items


def _compute_status(model: WanModel) -> ModelStatus:
    if model.experimental:
        return ModelStatus.EXPERIMENTAL
    if not model.has_core_model():
        return ModelStatus.INVALID
    configured = _configured_paths(model)
    missing = [item for item in configured if not Path(item[2]).exists()]
    core_missing = any(f in ("diffusion_model_path", "checkpoint_path") for f, _, _ in missing)
    if core_missing and not any(
        getattr(model, f) and Path(getattr(model, f)).exists()
        for f in ("diffusion_model_path", "checkpoint_path")
    ):
        return ModelStatus.MISSING
    if missing:
        return ModelStatus.PARTIAL
    return ModelStatus.OK


def add_model(data: dict) -> WanModel:
    with _REGISTRY_LOCK:
        models = _load_registry()
        data = normalize_legacy_entry(data)
        raw_id = (data.get("id") or data.get("display_name") or "").strip().lower()
        model_id = _SAFE_ID_RE.sub("_", raw_id.replace(" ", "_")).strip("_")
        if not model_id:
            raise ModelError("Model id or display name is required.")
        if any(m.id == model_id for m in models):
            raise ModelError(f"A model with id '{model_id}' already exists.")
        data = {**data, "id": model_id}
        model = WanModel.model_validate(data)
        model.status = _compute_status(model)
        models.append(model)
        _save_registry(models)
        logger.info("Model bundle added: %s (%s)", model.display_name, model.id)
        return model


def update_model(model_id: str, data: dict) -> WanModel:
    with _REGISTRY_LOCK:
        models = _load_registry()
        for i, model in enumerate(models):
            if model.id == model_id:
                merged = model.model_dump()
                data = normalize_legacy_entry(data)
                data.pop("id", None)
                merged.update(data)
                merged["updated_at"] = utc_now()
                updated = WanModel.model_validate(merged)
                updated.status = _compute_status(updated)
                models[i] = updated
                _save_registry(models)
                logger.info("Model bundle updated: %s", model_id)
                return updated
        raise ModelError(f"Model '{model_id}' not found in the registry.")


def remove_model(model_id: str) -> None:
    with _REGISTRY_LOCK:
        models = _load_registry()
        remaining = [m for m in models if m.id != model_id]
        if len(remaining) == len(models):
            raise ModelError(f"Model '{model_id}' not found in the registry.")
        _save_registry(remaining)
        logger.info("Model bundle removed: %s", model_id)


def set_default(model_id: str, mode: str) -> WanModel:
    if mode not in ("text2video", "image2video"):
        raise ModelError("Default mode must be 'text2video' or 'image2video'.")
    with _REGISTRY_LOCK:
        models = _load_registry()
        target = next((m for m in models if m.id == model_id), None)
        if target is None:
            raise ModelError(f"Model '{model_id}' not found in the registry.")
        compatible = (
            (ModelGenerationType.TEXT2VIDEO, ModelGenerationType.BOTH)
            if mode == "text2video"
            else (ModelGenerationType.IMAGE2VIDEO, ModelGenerationType.BOTH)
        )
        if target.generation_type not in compatible:
            raise ModelError(f"'{target.display_name}' does not support {mode} generation.")
        for model in models:
            if mode == "text2video":
                model.default_t2v = model.id == model_id
            else:
                model.default_i2v = model.id == model_id
        _save_registry(models)
        logger.info("Default %s model set to %s", mode, model_id)
        return target


def validate_model(model_id: str) -> ModelValidationResult:
    """Structured Model Bundle validation.

    Required: at least one core model (diffusion model or main checkpoint) that
    exists on disk. Every other configured component is checked; unconfigured
    core components are reported as informational, missing optional components
    never block validation.
    """
    model = get_model(model_id)
    checks: list[dict] = []
    missing_required: list[str] = []
    missing_optional: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []

    def check(name: str, ok: bool, detail: str, level: str = "info") -> None:
        checks.append({"name": name, "ok": ok, "detail": detail, "level": level})

    # --- core model requirement -------------------------------------------
    if not model.has_core_model():
        errors.append("No diffusion model or main checkpoint configured — the bundle cannot be used.")
        check("Core model configured", False, "Set a diffusion model path or a main checkpoint path", "error")
        missing_required.append("Diffusion model / main checkpoint")
    else:
        core_ok = False
        for field in ("diffusion_model_path", "checkpoint_path"):
            value = getattr(model, field)
            if value:
                exists = Path(value).exists()
                label = dict(CORE_COMPONENT_FIELDS)[field]
                check(label, exists, value, "error" if not exists else "info")
                if exists:
                    core_ok = True
                else:
                    missing_required.append(label)
        if not core_ok:
            errors.append("The configured core model files were not found on disk.")

    # --- other core components: checked when configured --------------------
    for field, label in CORE_COMPONENT_FIELDS:
        if field in ("diffusion_model_path", "checkpoint_path"):
            continue
        value = getattr(model, field)
        if not value:
            if field == "vision_encoder_path" and model.generation_type in (
                ModelGenerationType.IMAGE2VIDEO, ModelGenerationType.BOTH
            ):
                warnings.append("No vision encoder configured — Image2Video pipelines usually require one.")
                check(label, True, "Not configured (usually required for Image2Video)", "warning")
            continue
        exists = Path(value).exists()
        check(label, exists, value, "error" if not exists else "info")
        if not exists:
            missing_required.append(label)
            errors.append(f"{label} path not found: {value}")

    # --- optional components ------------------------------------------------
    for field, label in OPTIONAL_COMPONENT_FIELDS:
        value = getattr(model, field)
        paths = value if isinstance(value, list) else ([value] if value else [])
        for i, path in enumerate(paths):
            if not path:
                continue
            suffix = f" #{i + 1}" if isinstance(value, list) and len(paths) > 1 else ""
            exists = Path(path).exists()
            check(label + suffix, exists, path, "warning" if not exists else "info")
            if not exists:
                missing_optional.append(label + suffix)
                warnings.append(f"Optional {label.lower()}{suffix} not found: {path}")
    for name, path in model.custom_component_paths.items():
        if path:
            exists = Path(path).exists()
            check(f"Custom: {name}", exists, path, "warning" if not exists else "info")
            if not exists:
                missing_optional.append(f"Custom: {name}")

    # --- compatibility & flags ----------------------------------------------
    check("Generation type", True, f"Supports {model.generation_type.value}")
    if not (model.supports_direct_python_backend or model.supports_comfyui_export or model.supports_mock_backend):
        warnings.append("No backend compatibility flag is enabled for this bundle.")
    if model.experimental:
        warnings.append("Model is marked as experimental — results may vary.")
        check("Experimental flag", True, "Experimental bundle", "warning")

    # --- final status ---------------------------------------------------------
    if model.experimental:
        status = ModelStatus.EXPERIMENTAL
    elif errors and not model.has_core_model():
        status = ModelStatus.INVALID
    elif missing_required:
        core_found = any(
            getattr(model, f) and Path(getattr(model, f)).exists()
            for f in ("diffusion_model_path", "checkpoint_path")
        )
        status = ModelStatus.PARTIAL if core_found else ModelStatus.MISSING
    elif missing_optional:
        status = ModelStatus.PARTIAL
    else:
        status = ModelStatus.OK

    is_valid = status in (ModelStatus.OK, ModelStatus.EXPERIMENTAL) and not missing_required and not errors
    update_model(model_id, {"status": status.value})

    if is_valid:
        message = "Bundle is valid — all configured components were found."
    elif missing_required:
        message = f"Missing required components: {', '.join(missing_required)}."
    elif missing_optional:
        message = f"Bundle usable, but optional components are missing: {', '.join(missing_optional)}."
    else:
        message = "Bundle has configuration problems — see errors."

    logger.info("Model bundle validated: %s -> %s (valid=%s)", model_id, status.value, is_valid)
    return ModelValidationResult(
        model_id=model_id,
        status=status,
        is_valid=is_valid,
        checks=checks,
        missing_required_components=missing_required,
        missing_optional_components=missing_optional,
        warnings=warnings,
        errors=errors,
        notes=STATUS_LABELS.get(status.value, status.value),
        message=message,
    )


def model_supports_mode(model: WanModel, mode: str) -> bool:
    if model.generation_type == ModelGenerationType.BOTH:
        return True
    return model.generation_type.value == mode
