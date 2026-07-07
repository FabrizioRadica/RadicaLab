"""Wan model registry data models — Model Bundle edition.

A model entry is a *Model Bundle*: the structured group of every file a Wan
generation pipeline may need (diffusion model, checkpoint, VAE, text/CLIP/T5/
vision encoders, tokenizer, configs, LoRAs, control/upscaler/auxiliary models).
Not every installation requires all components; empty means "not configured".

Legacy single-path entries are normalized transparently (see
`normalize_legacy_entry`) so old registries and projects keep working.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ModelGenerationType(str, Enum):
    TEXT2VIDEO = "text2video"
    IMAGE2VIDEO = "image2video"
    BOTH = "both"


class ModelStatus(str, Enum):
    OK = "ok"
    MISSING = "missing"
    INVALID = "invalid"
    EXPERIMENTAL = "experimental"
    PARTIAL = "partial"  # partially configured bundle


STATUS_LABELS: dict[str, str] = {
    "ok": "OK",
    "missing": "Missing",
    "invalid": "Invalid",
    "experimental": "Experimental",
    "partial": "Partially Configured",
}

# Bundle component fields, in display order: (field, label, list_valued)
CORE_COMPONENT_FIELDS: list[tuple[str, str]] = [
    ("diffusion_model_path", "Diffusion model"),
    ("checkpoint_path", "Main checkpoint"),
    ("vae_path", "VAE"),
    ("text_encoder_path", "Text encoder"),
    ("clip_path", "CLIP encoder"),
    ("t5_encoder_path", "T5 encoder"),
    ("vision_encoder_path", "Vision encoder (I2V)"),
    ("tokenizer_path", "Tokenizer"),
    ("config_path", "Config"),
    ("scheduler_config_path", "Scheduler config"),
]

OPTIONAL_COMPONENT_FIELDS: list[tuple[str, str]] = [
    ("lora_paths", "LoRA files"),
    ("control_model_paths", "Control models"),
    ("upscaler_model_path", "Upscaler model"),
    ("auxiliary_model_paths", "Auxiliary models"),
]

LIST_COMPONENT_FIELDS = {"lora_paths", "control_model_paths", "auxiliary_model_paths"}


class WanModel(BaseModel):
    """A Wan Model Bundle."""

    # Identity
    id: str
    display_name: str
    family: str = "Wan2.2"
    version: str = "2.2"
    generation_type: ModelGenerationType = ModelGenerationType.TEXT2VIDEO

    # Backend compatibility
    supports_direct_python_backend: bool = False
    supports_comfyui_export: bool = True
    supports_mock_backend: bool = True
    backend_notes: str = ""

    # Core / semi-required component paths ("" = not configured)
    diffusion_model_path: str = ""
    checkpoint_path: str = ""
    vae_path: str = ""
    text_encoder_path: str = ""
    clip_path: str = ""
    t5_encoder_path: str = ""
    vision_encoder_path: str = ""
    tokenizer_path: str = ""
    config_path: str = ""
    scheduler_config_path: str = ""

    # Optional components
    lora_paths: list[str] = Field(default_factory=list)
    control_model_paths: list[str] = Field(default_factory=list)
    upscaler_model_path: str = ""
    auxiliary_model_paths: list[str] = Field(default_factory=list)
    custom_component_paths: dict[str, str] = Field(default_factory=dict)

    # Legacy field kept for backward compatibility with v1 registries
    extra_files_path: str = ""

    # Metadata
    recommended_vram_gb: int = 12
    recommended_resolutions: list[str] = Field(default_factory=lambda: ["1280x720"])
    supported_resolutions: list[str] = Field(default_factory=list)
    status: ModelStatus = ModelStatus.MISSING
    experimental: bool = False
    notes: str = ""
    default_t2v: bool = False
    default_i2v: bool = False
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)

    def component_snapshot(self) -> dict[str, Any]:
        """The exact component paths of this bundle — stored with every
        generation so later bundle edits do not rewrite history."""
        return {
            "model_bundle_id": self.id,
            "model_bundle_name": self.display_name,
            "generation_type": self.generation_type.value,
            "diffusion_model_path": self.diffusion_model_path,
            "checkpoint_path": self.checkpoint_path,
            "vae_path": self.vae_path,
            "text_encoder_path": self.text_encoder_path,
            "clip_path": self.clip_path,
            "t5_encoder_path": self.t5_encoder_path,
            "vision_encoder_path": self.vision_encoder_path,
            "tokenizer_path": self.tokenizer_path,
            "config_path": self.config_path,
            "scheduler_config_path": self.scheduler_config_path,
            "lora_paths": list(self.lora_paths),
            "control_model_paths": list(self.control_model_paths),
            "upscaler_model_path": self.upscaler_model_path,
            "auxiliary_model_paths": list(self.auxiliary_model_paths),
            "custom_component_paths": dict(self.custom_component_paths),
        }

    def core_paths_configured(self) -> list[str]:
        return [f for f, _ in CORE_COMPONENT_FIELDS if getattr(self, f)]

    def has_core_model(self) -> bool:
        """A bundle is usable only with a diffusion model or a main checkpoint."""
        return bool(self.diffusion_model_path or self.checkpoint_path)


def normalize_legacy_entry(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize old registry entries (v1 or ad-hoc) into bundle form.

    Handles: 'model_id'->'id', 'name'->'display_name', 'path'->'checkpoint_path',
    and legacy status strings. Unknown keys are dropped by pydantic.
    """
    data = dict(data)
    if "id" not in data and "model_id" in data:
        data["id"] = data.pop("model_id")
    if "display_name" not in data and "name" in data:
        data["display_name"] = data.pop("name")
    if not data.get("checkpoint_path") and data.get("path"):
        data["checkpoint_path"] = data.pop("path")
    if "is_default_t2v" in data and "default_t2v" not in data:
        data["default_t2v"] = data.pop("is_default_t2v")
    if "is_default_i2v" in data and "default_i2v" not in data:
        data["default_i2v"] = data.pop("is_default_i2v")
    status = str(data.get("status", "")).lower().replace(" ", "_")
    if status in ("partially_configured", "partially configured"):
        data["status"] = "partial"
    return data


class ModelValidationResult(BaseModel):
    model_id: str
    status: ModelStatus
    is_valid: bool = False
    checks: list[dict] = Field(default_factory=list)
    missing_required_components: list[str] = Field(default_factory=list)
    missing_optional_components: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    notes: str = ""
    message: str = ""
