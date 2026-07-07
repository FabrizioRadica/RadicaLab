"""Generation engine layer.

Backend adapter pattern:

- WanDirectBackend — the production backend. Runs REAL Wan 2.2+ inference
  through app/services/wan_backend.py (diffusers WanPipeline /
  WanImageToVideoPipeline with the full Model Bundle: DiT, VAE, UMT5 text
  encoder, tokenizer, optional LoRAs). If any required component or
  dependency is missing it fails with a clear error — it never writes a
  fake output file.

- MockGenerationBackend — DEVELOPER-ONLY simulated backend for UI testing
  without model files. It renders a clearly-watermarked procedural preview
  and flags every job and metadata file `mock_generation: true`. It is only
  available when GENERATION_BACKEND=mock is set explicitly AND APP_ENV is
  "development"; it is never selected silently.

Select the backend with GENERATION_BACKEND in .env ("wan" is the default).
"""

from __future__ import annotations

import math
import random
import threading
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw

from app.config import logger, settings
from app.models.generation_models import GenerationJob, GenerationParams, JobStatus
from app.models.project_models import GeneratedVideoEntry, Project
from app.services import (
    comfyui_service,
    gpu_memory_service,
    metadata_service,
    model_service,
    preset_service,
    project_service,
    sampler_registry,
    wan_backend,
)

ProgressCallback = Callable[[int, str], None]


class GenerationError(Exception):
    """Raised for generation failures with a user-readable message."""


class GenerationCancelled(Exception):
    """Raised when a running job is cancelled by the user (patch6b §4). Kept
    distinct from GenerationError so a cancelled job is never a 'failed' job."""


@dataclass
class BackendResult:
    output_path: Path
    preview_path: Path | None
    actual_format: str
    duration_seconds: float
    # Human-readable notes for sampling parameters the backend could not
    # apply — surfaced in the job log and stored in the video metadata so
    # nothing is silently ignored.
    unsupported_params: list[str] = field(default_factory=list)
    # Full backend diagnostics (effective settings, device/dtype maps, timings,
    # VRAM, warnings) — None for the mock backend (patch6 §4/§5).
    report: dict | None = None
    # Human-readable notes for changes a quality preset made (patch6 §12/§13).
    preset_notes: list[str] = field(default_factory=list)
    # Requested-vs-effective sampler/backend routing block (patch6c §12).
    sampler_backend: dict | None = None
    # ModelSamplingSD3 requested/effective/applied block (patch7 §14).
    model_sampling: dict | None = None


# High Quality preset floor for denoise steps (patch6 §13).
HIGH_QUALITY_MIN_STEPS = 30


def apply_quality_preset(project: Project) -> tuple[int, str, list[str]]:
    """Resolve the effective steps + offload mode for the selected quality
    preset. Returns (effective_steps, offload_mode, notes). Never silently
    overrides — every change is returned as a human-readable note (patch6 §12/§13)."""
    preset = (getattr(project.params, "quality_preset", "none") or "none").lower()
    steps = project.params.advanced.steps
    # Per-project offload override (patch8): a Wan preset (e.g. Low VRAM) can set
    # advanced.offload_policy; empty means use the env default.
    proj_policy = (getattr(project.params.advanced, "offload_policy", "") or "").lower()
    if proj_policy in settings._OFFLOAD_POLICY_TO_MODE:
        offload_mode = settings._OFFLOAD_POLICY_TO_MODE[proj_policy]
    else:
        offload_mode = settings.effective_offload_mode()
    notes: list[str] = []

    if preset == "comfyui_match":
        if offload_mode == "sequential":
            offload_mode = "model"
            notes.append("ComfyUI Match: offload policy changed from aggressive to "
                         "balanced to avoid hidden aggressive offload.")
        notes.append(
            "ComfyUI Match: requested sampler/scheduler/denoise are applied exactly "
            "when the direct backend supports them; unsupported values are reported "
            "as warnings. Exact parity with ComfyUI may not be possible with the "
            "direct diffusers backend — the sampler is UniPC flow-matching. Use the "
            "ComfyUI API backend for guaranteed parity."
        )
    elif preset == "high_quality":
        if steps < HIGH_QUALITY_MIN_STEPS:
            notes.append(f"High Quality preset changed steps from {steps} to "
                         f"{HIGH_QUALITY_MIN_STEPS}.")
            steps = HIGH_QUALITY_MIN_STEPS
        if offload_mode == "sequential":
            notes.append("High Quality preset changed offload policy from aggressive "
                         "to balanced.")
            offload_mode = "model"
    return steps, offload_mode, notes


# Sampling capability is now backed by the real sampler registry (patch6c §6/§7).
# The direct backend genuinely runs the samplers in
# sampler_registry.DIRECT_BACKEND_SAMPLERS; the UI reads this to show real
# direct-vs-ComfyUI capability instead of the old "UniPC is used" fiction.
def wan_sampling_capabilities() -> dict:
    """Sampler capability + active fallback policy for the backend-status API."""
    caps = sampler_registry.capabilities()
    caps.update({
        # Back-compat key consumed by older UI code: the samplers the direct
        # backend can really apply (no longer a hardcoded ["uni_pc"]).
        "sampler_names": caps["direct_backend_samplers"],
        "fallback_policy": settings.effective_sampler_fallback_policy(),
        "comfyui_available": comfyui_service.is_available(),
    })
    return caps


def wan_unsupported_sampling_params(params: GenerationParams) -> list[str]:
    """Notes for KSampler params the direct backend cannot apply *exactly*.

    Sampler support is handled by the fallback policy (resolve_sampling_plan);
    this only reports scheduler and denoise, which the flow-matching pipeline
    always fixes to its native schedule / full denoise. These are honest
    difference notes, never a claim that Wan cannot use the value (patch6c §5)."""
    notes: list[str] = []
    if (params.scheduler or "simple") not in ("simple", "normal"):
        notes.append(
            f"Scheduler '{params.scheduler}' has no separate effect in the direct "
            "backend — Wan's flow-matching sigma schedule is used. The value is "
            "preserved for ComfyUI export."
        )
    if abs(params.denoise - 1.0) > 1e-6:
        notes.append(
            f"Denoise {params.denoise:.2f} is not applied by the direct backend — it "
            "denoises from full noise (1.00). The value is preserved for ComfyUI "
            "export and is never remapped to CFG, motion strength or image influence."
        )
    return notes


def resolve_sampling_plan(project: Project, selected_backend: str = "direct",
                          confirm_fallback: bool = False):
    """Resolve which sampler/backend a generation will really use (patch6c §10).
    Applies the configured fallback policy; never returns a silent substitution."""
    return sampler_registry.resolve_sampling(
        project.params.sampler_name,
        selected_backend=selected_backend,
        fallback_policy=settings.effective_sampler_fallback_policy(),
        comfyui_available=comfyui_service.is_available(),
        warn_on_fallback=settings.wan_warn_on_sampler_fallback,
        confirm_fallback=confirm_fallback,
    )


# --------------------------------------------------------------------------
# ModelSamplingSD3 resolution (patch7)
# --------------------------------------------------------------------------

def resolve_model_sampling(project: Project) -> tuple[dict, list[str]]:
    """Resolve the requested ModelSamplingSD3 settings after quality presets
    (patch7 §15). Returns (settings_dict, preset_notes). Preset-driven changes
    are always reported — never hidden."""
    ms = project.params.model_sampling
    enabled = bool(ms.enabled)
    typ = (ms.type or "sd3")
    shift = float(ms.shift)
    notes: list[str] = []
    preset = (getattr(project.params, "quality_preset", "none") or "none").lower()

    if not enabled and preset in ("high_quality", "comfyui_match"):
        enabled = True
        typ = "sd3"
        if shift <= 0:
            shift = settings.wan_model_sampling_sd3_shift_default
        label = "High Quality" if preset == "high_quality" else "ComfyUI Match"
        notes.append(f"{label} preset enabled ModelSamplingSD3 with shift "
                     f"{shift:.2f} (verified to improve quality).")
    return {"enabled": enabled, "type": typ if enabled else "sd3", "shift": shift}, notes


def backend_supports_model_sampling_sd3(backend: str, effective_sampler: str) -> bool:
    """Whether a backend can apply ModelSamplingSD3 exactly (patch7 §16).

    - ComfyUI: yes, via the ModelSamplingSD3 node.
    - Direct: yes, because SD3 model sampling IS the flow-matching sigma shift and
      every direct-backend sampler is flow-matching, so the shift is applied
      exactly by the scheduler. If the sampler is not a direct flow-matching one
      (patch6c already blocks/routes those), the direct backend cannot apply it.
    """
    if backend == "comfyui":
        return True
    if backend == "direct":
        return sampler_registry.direct_backend_supports(effective_sampler)
    return False


@dataclass
class ModelSamplingResolution:
    """Outcome of resolving ModelSamplingSD3 against the render backend + policy
    (patch7 §8/§16). Nothing is silent."""
    requested: dict
    effective: dict
    applied: bool
    backend: str
    warnings: list = field(default_factory=list)
    blocked: bool = False
    blocked_reason: str | None = None
    needs_confirmation: bool = False
    confirmation_message: str | None = None
    routed_to_comfyui: bool = False

    def as_metadata(self) -> dict:
        return {
            "requested": self.requested,
            "effective": self.effective,
            "applied": self.applied,
            "applied_before_sampling": self.applied,
            "backend": self.backend,
            "routed_to_comfyui": self.routed_to_comfyui,
            "generation_started": not self.blocked,
            "blocked_reason": self.blocked_reason,
            "warnings": self.warnings,
        }


_MS_DISABLED_EFFECTIVE = {"enabled": False, "type": None, "shift": None}


def resolve_model_sampling_plan(project: Project, sampler_plan,
                                confirm_fallback: bool = False) -> ModelSamplingResolution:
    """Decide whether/where ModelSamplingSD3 is applied (patch7 §16). Rides the
    sampler routing: if the sampler already routed to ComfyUI, the modifier is
    applied there; otherwise the direct backend applies it as the flow shift.
    Never silently ignores an enabled modifier."""
    requested, preset_notes = resolve_model_sampling(project)

    # Disabled → nothing to apply, no fallback needed.
    if not requested["enabled"]:
        return ModelSamplingResolution(
            requested={"enabled": False, "type": None, "shift": None},
            effective=dict(_MS_DISABLED_EFFECTIVE), applied=False,
            backend=sampler_plan.effective_backend, warnings=list(preset_notes))

    backend = sampler_plan.effective_backend  # direct | comfyui (from sampler routing)
    eff_sampler = sampler_plan.effective_sampler or project.params.sampler_name
    warnings = list(preset_notes)

    if backend_supports_model_sampling_sd3(backend, eff_sampler):
        if backend == "comfyui" and settings.wan_warn_on_model_sampling_fallback:
            warnings.append("ModelSamplingSD3 is applied by the ComfyUI backend "
                            "(the render was already routed there).")
        return ModelSamplingResolution(
            requested=requested, effective=dict(requested), applied=True,
            backend=backend, warnings=warnings,
            routed_to_comfyui=(backend == "comfyui"))

    # Direct backend cannot apply it (e.g. non-flow sampler) — apply the policy.
    policy = settings.effective_model_sampling_fallback_policy()
    comfy_ok = comfyui_service.is_available()
    if policy == "route_to_comfyui" and comfy_ok:
        if settings.wan_warn_on_model_sampling_fallback:
            warnings.append("Direct backend cannot apply ModelSamplingSD3; "
                            "generation routed to the ComfyUI backend for exact behavior.")
        return ModelSamplingResolution(
            requested=requested, effective=dict(requested), applied=True,
            backend="comfyui", warnings=warnings, routed_to_comfyui=True)
    if policy == "ask" and not confirm_fallback:
        return ModelSamplingResolution(
            requested=requested, effective=dict(_MS_DISABLED_EFFECTIVE), applied=False,
            backend=backend, warnings=warnings, needs_confirmation=True,
            confirmation_message=(
                "The selected direct backend cannot apply ModelSamplingSD3. "
                "Switch to the ComfyUI backend for this render?"))
    if policy == "ask" and confirm_fallback:
        warnings.append("ModelSamplingSD3 could not be applied (direct backend "
                        "unsupported) and the user confirmed proceeding without it.")
        return ModelSamplingResolution(
            requested=requested, effective=dict(_MS_DISABLED_EFFECTIVE), applied=False,
            backend=backend, warnings=warnings)
    # block (default when not routable)
    return ModelSamplingResolution(
        requested=requested, effective=dict(_MS_DISABLED_EFFECTIVE), applied=False,
        backend=backend, warnings=warnings, blocked=True,
        blocked_reason=(
            "ModelSamplingSD3 is enabled, but the selected direct backend cannot "
            "apply it and ComfyUI routing is unavailable. Enable the ComfyUI backend "
            "(COMFYUI_API_ENABLED) or disable ModelSamplingSD3."))


def model_sampling_capabilities() -> dict:
    """ModelSamplingSD3 capability + defaults for the backend-status API (patch7 §13)."""
    return {
        "types": ["sd3"],
        "enabled_default": settings.wan_model_sampling_sd3_enabled_default,
        "shift_default": settings.wan_model_sampling_sd3_shift_default,
        "fallback_policy": settings.effective_model_sampling_fallback_policy(),
        "direct_backend_supported": True,
        "comfyui_available": comfyui_service.is_available(),
    }


def _never_cancel() -> bool:
    return False


class BaseGenerationBackend(ABC):
    name = "base"
    is_mock = False

    @abstractmethod
    def generate(self, project: Project, seed: int, output_stem: str, output_dir: Path,
                 preview_dir: Path, progress: ProgressCallback,
                 should_cancel: Callable[[], bool] = _never_cancel,
                 confirm_fallback: bool = False) -> BackendResult:
        ...


class WanDirectBackend(BaseGenerationBackend):
    """Direct Python Wan 2.2+ execution via diffusers (real inference).

    Resolves the full Model Bundle (diffusion model/DiT, VAE, UMT5 text
    encoder, tokenizer, scheduler config, LoRAs) and runs the actual
    WanPipeline / WanImageToVideoPipeline. Every failure surfaces as a
    GenerationError with the exact missing component or runtime problem.
    """

    name = "wan"
    is_mock = False

    def generate(self, project, seed, output_stem, output_dir, preview_dir, progress,
                 should_cancel=_never_cancel, confirm_fallback=False):
        model = model_service.get_model(project.model_id)
        bundle = model.component_snapshot()
        fmt = project.params.output_format.lower()
        if fmt not in ("mp4", "webm"):
            fmt = "mp4"
        output_path = output_dir / f"{output_stem}.{fmt}"
        preview_path = preview_dir / f"{output_stem}.jpg"

        source_image = None
        if project.generation_mode.value == "image2video" and project.source_image:
            source_image = (project_service.project_dir(project.folder)
                            / "source" / project.source_image)

        effective_steps, offload_mode, preset_notes = apply_quality_preset(project)
        sampling_notes = wan_unsupported_sampling_params(project.params)

        # patch6c §10 — resolve the real sampler/backend BEFORE rendering. No
        # silent fallback: block, route to ComfyUI, or use the exact sampler.
        plan = resolve_sampling_plan(project, confirm_fallback=confirm_fallback)
        if plan.blocked:
            raise GenerationError(plan.blocked_reason)
        if plan.needs_confirmation:
            raise GenerationError(
                plan.confirmation_message + " (Confirm to proceed, or set "
                "WAN_SAMPLER_FALLBACK_POLICY to route_to_comfyui / a supported sampler.)")

        # patch7 §16 — resolve ModelSamplingSD3 against the (already-resolved)
        # sampler backend. Never silently ignored: block / route / apply.
        ms_plan = resolve_model_sampling_plan(project, plan, confirm_fallback=confirm_fallback)
        if ms_plan.blocked:
            raise GenerationError(ms_plan.blocked_reason)
        if ms_plan.needs_confirmation:
            raise GenerationError(
                ms_plan.confirmation_message + " (Confirm to proceed, or set "
                "WAN_MODEL_SAMPLING_FALLBACK_POLICY to route_to_comfyui, or disable "
                "ModelSamplingSD3.)")
        sampling_notes = list(plan.warnings) + list(ms_plan.warnings) + sampling_notes

        # patch6c §8/§11 + patch7 §11 — route to ComfyUI when the sampler OR the
        # model-sampling modifier requires it for exact behavior.
        if plan.routed_to_comfyui or ms_plan.routed_to_comfyui:
            return self._render_via_comfyui(project, seed, output_path, preview_path,
                                            fmt, plan, ms_plan, sampling_notes, preset_notes)

        ms_eff = ms_plan.effective
        try:
            report = wan_backend.generate_video(
                bundle=bundle,
                mode=project.generation_mode.value,
                prompt=project.composed_prompt(),
                negative_prompt=project.negative_prompt,
                width=project.resolution.width,
                height=project.resolution.height,
                frames=project.params.frames,
                fps=project.params.fps,
                steps=effective_steps,
                guidance_scale=project.params.guidance_scale,
                seed=seed,
                output_path=output_path,
                preview_path=preview_path,
                source_image=source_image,
                sampler_name=project.params.sampler_name,
                effective_sampler=plan.effective_sampler or "",
                scheduler=project.params.scheduler,
                denoise=project.params.denoise,
                model_sampling_enabled=bool(ms_eff.get("enabled")),
                model_sampling_type=ms_eff.get("type") or "sd3",
                model_sampling_shift=float(ms_eff.get("shift") or project.params.model_sampling.shift),
                requested_precision=project.params.advanced.precision,
                offload_mode=offload_mode,
                extra_warnings=sampling_notes,
                should_cancel=should_cancel,
                progress=progress,
            )
        except wan_backend.WanGenerationCancelled as exc:
            raise GenerationCancelled() from exc
        except wan_backend.WanBackendError as exc:
            raise GenerationError(str(exc)) from exc

        # Record the actual effective sampler the backend built (authoritative).
        sampler_backend = plan.as_metadata()
        eff = report.effective or {}
        sampler_backend["effective_sampler_name"] = eff.get("sampler_name", plan.effective_sampler)
        sampler_backend["effective_scheduler"] = eff.get("scheduler")
        sampler_backend["requested_scheduler"] = project.params.scheduler

        # The backend's report carries the authoritative model_sampling block.
        model_sampling = report.model_sampling or ms_plan.as_metadata()
        if ms_plan.warnings:
            model_sampling = dict(model_sampling)
            model_sampling["warnings"] = list(dict.fromkeys(
                list(model_sampling.get("warnings", [])) + list(ms_plan.warnings)))

        return BackendResult(output_path, preview_path, fmt, report.duration_seconds,
                             unsupported_params=sampling_notes,
                             report=report.as_dict(), preset_notes=preset_notes,
                             sampler_backend=sampler_backend,
                             model_sampling=model_sampling)

    def _render_via_comfyui(self, project, seed, output_path, preview_path, fmt,
                            plan, ms_plan, sampling_notes, preset_notes) -> BackendResult:
        """Render through the ComfyUI API so the requested sampler AND
        ModelSamplingSD3 are really used (patch6c §8/§11, patch7 §11). Any failure
        raises — never a silent direct fallback."""
        from app.services import workflow_export_service

        reason = f"requested sampler '{plan.requested_sampler}'"
        if ms_plan.routed_to_comfyui and not plan.routed_to_comfyui:
            reason = "ModelSamplingSD3"
        logger.info("Routing to ComfyUI backend — %s used exactly there.", reason)
        # The exported workflow includes ModelSamplingSD3 (patch7 §12) so the
        # modifier the user requested is really applied on the ComfyUI side.
        workflow = workflow_export_service.build_workflow(project, seed_used=seed)
        try:
            info = comfyui_service.render(workflow, output_path, preview_path)
        except comfyui_service.ComfyUIError as exc:
            raise GenerationError(
                f"Routed to ComfyUI for {reason}, but the ComfyUI render failed: "
                f"{exc}") from exc

        duration = round(project.params.frames / max(project.params.fps, 1), 3)
        sampler_backend = plan.as_metadata()
        sampler_backend["effective_render_backend"] = "comfyui"
        sampler_backend["effective_scheduler"] = project.params.scheduler
        sampler_backend["requested_scheduler"] = project.params.scheduler
        sampler_backend.update({k: v for k, v in info.items()
                                if k in ("prompt_id", "comfyui_url")})
        model_sampling = ms_plan.as_metadata()
        report = {
            "effective_settings": {
                "sampler_name": plan.effective_sampler,
                "scheduler": project.params.scheduler,
                "steps": project.params.advanced.steps,
                "cfg": project.params.guidance_scale,
                "denoise": project.params.denoise,
                "resolution": project.resolution.label(),
                "frames": project.params.frames,
                "fps": project.params.fps,
                "seed": seed,
                "requested_sampler_name": project.params.sampler_name,
                "model_sampling_sd3": bool(ms_plan.effective.get("enabled")),
                "model_sampling_shift": ms_plan.effective.get("shift"),
            },
            "warnings": list(sampling_notes),
            "effective_render_backend": "comfyui",
            "model_sampling": model_sampling,
        }
        preview = preview_path if preview_path.exists() else None
        return BackendResult(output_path, preview, fmt, duration,
                             unsupported_params=sampling_notes,
                             report=report, preset_notes=preset_notes,
                             sampler_backend=sampler_backend,
                             model_sampling=model_sampling)


class MockGenerationBackend(BaseGenerationBackend):
    """DEVELOPER-ONLY simulated backend for UI testing without model files.

    Never used in production: get_backend() only selects it when
    GENERATION_BACKEND=mock is set explicitly and APP_ENV=development.
    Its output is watermarked "MOCK PREVIEW" and flagged in all metadata.
    """

    name = "mock"
    is_mock = True

    STAGES = [
        (5, "Validating parameters"),
        (12, "Loading model (simulated)"),
        (25, "Encoding prompts (simulated)"),
        (40, "Sampling frames (simulated)"),
        (70, "Rendering preview frames"),
        (88, "Encoding output"),
        (96, "Writing metadata"),
    ]

    def generate(self, project, seed, output_stem, output_dir, preview_dir, progress,
                 should_cancel=_never_cancel, confirm_fallback=False):
        rng = random.Random(seed)
        for pct, stage in self.STAGES[:4]:
            if should_cancel():
                raise GenerationCancelled()
            progress(pct, stage)
            time.sleep(0.5)

        if should_cancel():
            raise GenerationCancelled()
        progress(70, "Rendering preview frames")
        frames = self._render_frames(project, rng)
        if should_cancel():
            raise GenerationCancelled()
        progress(88, "Encoding output")
        output_path, actual_format = self._encode_output(frames, project, output_stem, output_dir)
        preview_path = preview_dir / f"{output_stem}.jpg"
        frames[0].convert("RGB").save(preview_path, quality=85)
        progress(96, "Writing metadata")
        duration = project.params.frames / max(project.params.fps, 1)
        return BackendResult(output_path, preview_path, actual_format, duration)

    def _encode_output(self, frames: list[Image.Image], project: Project,
                       output_stem: str, output_dir: Path) -> tuple[Path, str]:
        """Encode mock frames as real video (mp4/webm via bundled ffmpeg),
        falling back to animated WebP if no encoder is available."""
        fmt = project.params.output_format.lower()
        if fmt not in ("mp4", "webm"):
            fmt = "mp4"
        try:
            import imageio_ffmpeg

            w, h = frames[0].size
            fps = min(max(project.params.fps, 1), 60)
            output_path = output_dir / f"{output_stem}.{fmt}"
            codec = "libx264" if fmt == "mp4" else "libvpx-vp9"
            extra = ["-crf", "23", "-preset", "medium"] if fmt == "mp4" else ["-b:v", "1M"]
            writer = imageio_ffmpeg.write_frames(
                str(output_path), (w, h), fps=fps, codec=codec,
                pix_fmt_in="rgb24", output_params=extra,
            )
            writer.send(None)
            # Hold each rendered frame long enough to match the requested
            # duration (frames param) at the target fps.
            total = max(project.params.frames, len(frames))
            for i in range(total):
                frame = frames[min(int(i / total * len(frames)), len(frames) - 1)]
                writer.send(frame.convert("RGB").tobytes())
            writer.close()
            return output_path, fmt
        except Exception as exc:  # noqa: BLE001 — any encoder failure falls back to WebP
            logger.warning("ffmpeg encoding unavailable (%s) — falling back to animated WebP", exc)
            output_path = output_dir / f"{output_stem}.webp"
            duration_ms = int(1000 / max(project.params.fps, 1)) * 2
            frames[0].save(
                output_path, save_all=True, append_images=frames[1:],
                duration=max(duration_ms, 40), loop=0, quality=80, method=4,
            )
            return output_path, "webp"

    def _canvas_size(self, project: Project) -> tuple[int, int]:
        w, h = project.resolution.width, project.resolution.height
        scale = min(1.0, 480 / max(w, h))
        w, h = max(int(w * scale), 64), max(int(h * scale), 64)
        # H.264 requires even dimensions.
        return w - (w % 2), h - (h % 2)

    def _render_frames(self, project: Project, rng: random.Random, single: bool = False) -> list[Image.Image]:
        w, h = self._canvas_size(project)
        n = 1 if single else max(12, min(project.params.frames, 40))
        hue_shift = rng.random()
        cm = project.camera_motion
        movement = cm.movement_type.value if cm.enabled else "static"
        motion = project.params.motion_strength

        source_img = None
        if project.generation_mode.value == "image2video" and project.source_image:
            src = project_service.project_dir(project.folder) / "source" / project.source_image
            if src.exists():
                try:
                    source_img = Image.open(src).convert("RGB").resize((w, h))
                except OSError:
                    source_img = None

        frames: list[Image.Image] = []
        for i in range(n):
            t = i / max(n - 1, 1)
            frames.append(self._render_frame(project, w, h, t, hue_shift, movement, motion, source_img))
        return frames

    def _render_frame(self, project, w, h, t, hue_shift, movement, motion, source_img) -> Image.Image:
        # Camera-motion-dependent drift so the mock visibly reacts to settings.
        amp = 0.12 + 0.25 * motion
        dx = dy = 0.0
        zoom = 1.0
        if movement in ("pan_left", "truck_left"):
            dx = amp * t
        elif movement in ("pan_right", "truck_right"):
            dx = -amp * t
        elif movement in ("tilt_up", "crane_up"):
            dy = amp * t
        elif movement in ("tilt_down", "crane_down"):
            dy = -amp * t
        elif movement == "push_in":
            zoom = 1.0 + 0.35 * motion * t
        elif movement == "pull_back":
            zoom = 1.35 - 0.35 * motion * t
        elif movement in ("orbit_left", "orbit_right", "parallax", "follow"):
            sign = -1 if movement == "orbit_left" else 1
            dx = sign * amp * math.sin(2 * math.pi * t)
            zoom = 1.0 + 0.08 * math.sin(2 * math.pi * t + math.pi / 2)
        elif movement == "handheld":
            dx = amp * 0.25 * math.sin(12 * math.pi * t)
            dy = amp * 0.2 * math.cos(9 * math.pi * t)

        if source_img is not None:
            zw, zh = int(w * zoom * 1.2), int(h * zoom * 1.2)
            img = source_img.resize((max(zw, w), max(zh, h)))
            ox = int((img.width - w) / 2 + dx * w)
            oy = int((img.height - h) / 2 + dy * h)
            ox = min(max(ox, 0), img.width - w)
            oy = min(max(oy, 0), img.height - h)
            img = img.crop((ox, oy, ox + w, oy + h))
        else:
            img = Image.new("RGB", (w, h))
            draw = ImageDraw.Draw(img)
            top = self._hue_color(hue_shift, 0.25)
            bottom = self._hue_color((hue_shift + 0.12) % 1.0, 0.08)
            for y in range(h):
                f = y / max(h - 1, 1)
                color = tuple(int(top[c] * (1 - f) + bottom[c] * f) for c in range(3))
                draw.line([(0, y), (w, y)], fill=color)
            # drifting "sun"
            sun_x = int(w * (0.5 + dx * 2 + 0.25 * math.sin(2 * math.pi * (t * 0.5 + hue_shift))))
            sun_y = int(h * (0.35 + dy * 2))
            sun_r = int(min(w, h) * 0.12 * zoom)
            draw.ellipse([sun_x - sun_r, sun_y - sun_r, sun_x + sun_r, sun_y + sun_r],
                         fill=self._hue_color((hue_shift + 0.5) % 1.0, 0.9))
            # layered "mountains" with parallax
            for layer, (frac, shade) in enumerate(((0.62, 0.20), (0.74, 0.14), (0.86, 0.08))):
                par = dx * (1.5 - layer * 0.4) * w
                points = [(0, h)]
                for step in range(9):
                    px = step / 8 * w
                    py = h * frac + math.sin(step * 1.7 + layer * 2.3 + hue_shift * 10) * h * 0.06 * zoom
                    points.append((px + par, py))
                points.append((w, h))
                draw.polygon(points, fill=self._hue_color((hue_shift + 0.05 * layer) % 1.0, shade))
            draw = ImageDraw.Draw(img)

        # Clear MOCK banner — this output must never look like real generation.
        draw = ImageDraw.Draw(img)
        banner_h = max(14, h // 16)
        draw.rectangle([0, h - banner_h, w, h], fill=(12, 10, 24))
        draw.text((6, h - banner_h + 2), f"MOCK PREVIEW — {project.name[:38]}", fill=(196, 181, 253))
        return img

    @staticmethod
    def _hue_color(hue: float, value: float) -> tuple[int, int, int]:
        import colorsys

        r, g, b = colorsys.hsv_to_rgb(hue, 0.55, max(min(value, 1.0), 0.0))
        return int(r * 255), int(g * 255), int(b * 255)


_BACKENDS = {"mock": MockGenerationBackend, "wan": WanDirectBackend}


def get_backend() -> BaseGenerationBackend:
    """Select the generation backend. Never falls back to mock silently:
    an unknown value is an error, and mock requires APP_ENV=development."""
    name = settings.generation_backend
    if name not in _BACKENDS:
        raise GenerationError(
            f"Unknown GENERATION_BACKEND '{name}' in .env — valid values are 'wan' "
            "(real generation) or 'mock' (developer-only simulation)."
        )
    if name == "mock" and settings.app_env.lower() not in ("development", "dev"):
        raise GenerationError(
            "GENERATION_BACKEND=mock is a developer-only setting and is disabled "
            f"because APP_ENV is '{settings.app_env}'. Set GENERATION_BACKEND=wan "
            "for real generation, or APP_ENV=development to test the UI with "
            "simulated output."
        )
    return _BACKENDS[name]()


class JobManager:
    """In-memory generation job queue with worker threads."""

    def __init__(self) -> None:
        self._jobs: dict[str, GenerationJob] = {}
        self._lock = threading.Lock()
        self._slots = threading.Semaphore(max(settings.max_parallel_jobs, 1))
        # Per-job cancellation flags checked by the running backend (patch6b §6).
        self._cancel_flags: dict[str, threading.Event] = {}

    def submit(self, project: Project, confirm_fallback: bool = False) -> GenerationJob:
        model = self._validate(project, confirm_fallback=confirm_fallback)
        job = GenerationJob(
            id=uuid.uuid4().hex[:12],
            project_id=project.id,
            project_name=project.name,
            mode=project.generation_mode,
            model_id=project.model_id,
            backend=settings.generation_backend,
            is_mock=get_backend().is_mock,
            confirm_fallback=confirm_fallback,
            # Snapshot of the model bundle components resolved at submit time —
            # this is what the backend receives and what metadata will record.
            model_bundle=model.component_snapshot(),
        )
        with self._lock:
            self._jobs[job.id] = job
            self._cancel_flags[job.id] = threading.Event()
        thread = threading.Thread(target=self._run, args=(job.id,), daemon=True)
        thread.start()
        logger.info("Generation job %s queued for project '%s' (backend=%s)", job.id, project.name, job.backend)
        return job

    def get(self, job_id: str) -> GenerationJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def request_cancel(self, job_id: str) -> dict:
        """Flag a running/pending job for cancellation (patch6b §5). Non-blocking:
        the worker thread performs the actual stop and cleanup. Returns the
        updated status. Raises GenerationError for unknown/non-cancellable jobs."""
        from app.models.generation_models import CANCELLABLE_STATUSES
        from app.models.project_models import utc_now

        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise GenerationError(f"Job '{job_id}' not found.")
            if job.status not in CANCELLABLE_STATUSES:
                raise GenerationError(
                    f"Job '{job_id}' is {job.status.value} and can no longer be "
                    "cancelled.")
            already = job.cancel_requested
            job.cancel_requested = True
            if not job.cancel_requested_at:
                job.cancel_requested_at = utc_now()
            if job.status in (JobStatus.PENDING, JobStatus.RUNNING):
                job.status = JobStatus.CANCEL_REQUESTED
                job.stage = "Stopping…"
            flag = self._cancel_flags.get(job_id)
        if flag is not None:
            flag.set()
        if not already:
            self._log(job_id, "Cancellation requested by user.")
            logger.info("Cancellation requested for job %s", job_id)
        return {
            "job_id": job_id,
            "status": JobStatus.CANCEL_REQUESTED.value,
            "message": "Cancellation requested.",
        }

    def list(self, project_id: str | None = None) -> list[GenerationJob]:
        with self._lock:
            jobs = list(self._jobs.values())
        if project_id:
            jobs = [j for j in jobs if j.project_id == project_id]
        return sorted(jobs, key=lambda j: j.created_at, reverse=True)

    def _validate(self, project: Project, confirm_fallback: bool = False):
        if not project.positive_prompt.strip():
            raise GenerationError("The positive prompt is empty. Write a prompt before generating.")
        if project.resolution.width % 8 or project.resolution.height % 8:
            raise GenerationError("Resolution width and height must be multiples of 8.")
        if project.params.frames < 1 or project.params.fps < 1:
            raise GenerationError("Frame count and FPS must be positive values.")
        try:
            model = model_service.get_model(project.model_id)
        except model_service.ModelError as exc:
            raise GenerationError(str(exc)) from exc
        if not model_service.model_supports_mode(model, project.generation_mode.value):
            raise GenerationError(
                f"Model '{model.display_name}' does not support {project.generation_mode.value} generation. "
                "Pick a compatible model in the Model Manager."
            )
        if project.generation_mode.value == "image2video":
            if not project.source_image:
                raise GenerationError("Image2Video requires a source image. Upload one first.")
            src = project_service.project_dir(project.folder) / "source" / project.source_image
            if not src.exists():
                raise GenerationError("The project source image file is missing on disk. Upload it again.")
        backend = get_backend()
        if not backend.is_mock and model.status.value in ("missing", "invalid"):
            raise GenerationError(
                f"Model files for '{model.display_name}' are {model.status.value}. Validate the model in the Model Manager."
            )
        if backend.is_mock and not model.supports_mock_backend:
            raise GenerationError(
                f"Bundle '{model.display_name}' has the mock/test backend disabled. Enable it in the Model Manager."
            )
        if backend.name == "wan":
            # patch6c §10 — validate sampler compatibility at submit time so the
            # user is blocked immediately (never a silent fallback, never a
            # started-then-failed job) when the sampler cannot be honored.
            plan = resolve_sampling_plan(project, confirm_fallback=confirm_fallback)
            if plan.blocked:
                raise GenerationError(plan.blocked_reason)
            if plan.needs_confirmation:
                raise GenerationError(
                    plan.confirmation_message + " (Confirm to proceed, or set "
                    "WAN_SAMPLER_FALLBACK_POLICY to route_to_comfyui / a supported sampler.)")
            # patch7 §16 — validate ModelSamplingSD3 at submit time too, so an
            # enabled-but-unapplicable modifier blocks immediately instead of a
            # started-then-failed job (never a silent ignore).
            ms_plan = resolve_model_sampling_plan(project, plan, confirm_fallback=confirm_fallback)
            if ms_plan.blocked:
                raise GenerationError(ms_plan.blocked_reason)
            if ms_plan.needs_confirmation:
                raise GenerationError(
                    ms_plan.confirmation_message + " (Confirm to proceed, or set "
                    "WAN_MODEL_SAMPLING_FALLBACK_POLICY to route_to_comfyui, or disable "
                    "ModelSamplingSD3.)")
            # Full runtime preflight at submit time so the user gets an
            # immediate, precise error instead of a failed background job:
            # dependencies, bundle component files, device, params, memory.
            try:
                wan_backend.preflight(
                    model.component_snapshot(), project.generation_mode.value,
                    project.resolution.width, project.resolution.height,
                    project.params.frames, project.params.fps,
                )
            except wan_backend.WanBackendError as exc:
                raise GenerationError(str(exc)) from exc
        return model

    def _set(self, job_id: str, **updates) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            for key, value in updates.items():
                setattr(job, key, value)

    def _log(self, job_id: str, message: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.log.append(message)

    def _log_disk_state(self, job_id: str) -> None:
        """Record disk warning/critical state in the job log (never blocks)."""
        try:
            from app.services import system_monitor_service

            disk = system_monitor_service.get_system_stats().get("disk") or {}
            if not disk.get("available"):
                return
            pct = disk.get("percent")
            if disk.get("critical"):
                self._log(job_id, f"WARNING: Disk usage is high: {pct}%. Video generation "
                                  "may fail because of insufficient free space.")
            elif disk.get("warning"):
                self._log(job_id, f"WARNING: Disk usage is above "
                                  f"{settings.system_monitor_disk_warning_percent}% ({pct}%). "
                                  "Consider freeing space before long generations.")
        except Exception:  # noqa: BLE001 — disk stats must never break a job
            pass

    @staticmethod
    def _apply_control_after_generate(project: Project, seed_used: int) -> str | None:
        """Update the stored seed after a successful generation (§ KSampler
        'control after generate'). The `-1 = random seed` behavior is
        preserved: random-seed projects keep their random seed untouched."""
        mode = project.params.control_after_generate
        if mode == "fixed":
            return None
        if project.params.random_seed or project.params.seed < 0:
            # Random seeds are resolved per-job only; the stored -1 stays.
            return None
        if mode == "randomize":
            project.params.seed = random.randint(0, 2**31 - 1)
        elif mode == "increment":
            project.params.seed = min(project.params.seed + 1, 2**31 - 1)
        elif mode == "decrement":
            project.params.seed = max(project.params.seed - 1, 0)
        else:
            return None
        return f"Seed updated for next generation ({mode}): {project.params.seed}"

    def _run(self, job_id: str) -> None:
        from app.models.project_models import utc_now

        self._slots.acquire()
        try:
            job = self.get(job_id)
            if job is None:
                return
            project = project_service.load_project(job.project_id)
            backend = get_backend()

            seed = project.params.seed
            if project.params.random_seed or seed < 0:
                seed = random.randint(0, 2**31 - 1)

            self._set(job_id, status=JobStatus.RUNNING, started_at=utc_now(), stage="Starting", seed_used=seed)
            self._log(job_id, f"Job started with backend '{backend.name}' (seed {seed})")
            self._log_disk_state(job_id)
            bundle = job.model_bundle or {}
            if bundle:
                configured = [k for k, v in bundle.items()
                              if v and k.endswith(("_path", "_paths")) and v != []]
                self._log(job_id, f"Model bundle '{bundle.get('model_bundle_name', job.model_id)}' "
                                  f"resolved ({len(configured)} component path(s) configured)")
            logger.info("Generation started: job %s, project '%s', seed %s", job_id, project.name, seed)

            pdir = project_service.project_dir(project.folder)
            output_dir = pdir / "outputs"
            preview_dir = pdir / "previews"
            output_dir.mkdir(exist_ok=True)
            preview_dir.mkdir(exist_ok=True)
            index = project_service.next_output_index(project)
            stem = f"{project_service.sanitize_folder_name(project.name)}_{index:04d}"

            flag = self._cancel_flags.get(job_id)
            should_cancel = flag.is_set if flag is not None else _never_cancel

            def progress(pct: int, stage: str) -> None:
                if should_cancel():
                    self._set(job_id, progress=pct, stage="Stopping…")
                    return
                self._set(job_id, progress=pct, stage=stage)
                self._log(job_id, f"[{pct:3d}%] {stage}")

            result = backend.generate(project, seed, stem, output_dir, preview_dir,
                                      progress, should_cancel=should_cancel,
                                      confirm_fallback=job.confirm_fallback)
            for note in result.preset_notes:
                self._log(job_id, f"PRESET: {note}")
                logger.info("Job %s preset: %s", job_id, note)
            for note in result.unsupported_params:
                self._log(job_id, f"WARNING: {note}")
                logger.warning("Job %s: %s", job_id, note)
            # Surface every backend fallback warning in the job log (patch6 §3).
            report = result.report or {}
            for note in report.get("warnings", []):
                if note not in result.unsupported_params:
                    self._log(job_id, f"WARNING: {note}")
                    logger.warning("Job %s: %s", job_id, note)
            # Log the sampler/backend routing outcome (patch6c §13).
            if result.sampler_backend:
                sb = result.sampler_backend
                self._log(job_id, "Sampler: requested='{}' effective='{}' backend={}{}".format(
                    sb.get("requested_sampler_name"), sb.get("effective_sampler_name"),
                    sb.get("effective_render_backend"),
                    " (routed to ComfyUI)" if sb.get("routed_to_comfyui") else ""))
            # Log the ModelSamplingSD3 outcome (patch7 §13).
            if result.model_sampling:
                ms = result.model_sampling
                eff = ms.get("effective") or {}
                if ms.get("applied"):
                    self._log(job_id, "ModelSamplingSD3: applied (shift={}) on {} backend".format(
                        eff.get("shift"), ms.get("backend")))
                elif (ms.get("requested") or {}).get("enabled"):
                    self._log(job_id, "ModelSamplingSD3: requested but NOT applied "
                                      "(see warnings).")
            # VRAM cleanup after a successful render (patch8 §14). Runs before
            # metadata is written so the report is recorded; a cleanup failure is
            # a warning, never a failed render.
            gen_cleanup = None
            if settings.wan_clear_temp_vram_after_generation:
                # Per-project unload override (Low VRAM preset) OR the global flag.
                unload_after = (settings.wan_unload_model_after_generation
                                or bool(getattr(project.params.advanced,
                                                "unload_model_after_generation", False)))
                gen_cleanup = self._run_vram_cleanup(
                    job_id, f"job_completed:{job_id}", unload_after)
            vram_cleanup = {"after_generation": gen_cleanup} if gen_cleanup else None

            if report:
                # Attach diagnostics to the job for the Backend Diagnostics panel,
                # including the requested settings so the panel can show
                # requested-vs-effective pairs (patch6 §4/§5, patch6c §13, patch7 §13).
                report = dict(report)
                report["requested_settings"] = metadata_service._requested_settings(
                    project, seed, job.model_bundle or {})
                report["preset_notes"] = result.preset_notes
                report["sampler_backend"] = result.sampler_backend
                report["model_sampling"] = result.model_sampling
                report["vram_cleanup"] = vram_cleanup
                report["vram_cleanup_settings"] = gpu_memory_service.cleanup_settings()
                # Detected model speed profile + turbo/normal mismatch warning
                # (patchoptimization §9/§10). Derived, never rewrites values.
                try:
                    profile_meta = preset_service.generation_profile_metadata(
                        project, job.model_bundle or {})
                    report["wan_model_profile"] = profile_meta
                    if profile_meta.get("preset_warning"):
                        self._log(job_id, "Preset/model profile: "
                                  + profile_meta["preset_warning"])
                    if profile_meta.get("turbo_parameter_warning"):
                        self._log(job_id, "⚠ " + (profile_meta.get("turbo_parameter_message")
                                                  or "Turbo model with high CFG/steps."))
                    else:
                        self._log(job_id, "Detected model profile: "
                                  + str(profile_meta.get("detected_model_profile")))
                except Exception as exc:  # noqa: BLE001 — never break a finished render
                    logger.warning("Model profile diagnostics failed: %s", exc)
                self._set(job_id, diagnostics=report, preset_notes=result.preset_notes)
                timings = report.get("timings") or {}
                if timings.get("total_seconds"):
                    self._log(job_id, "Timing breakdown (s): " + ", ".join(
                        f"{k.replace('_seconds', '')}={v}" for k, v in timings.items() if v))
                devmap = report.get("device_map") or {}
                if devmap:
                    self._log(job_id, "Device map: " + ", ".join(
                        f"{k}={v}" for k, v in devmap.items()))

            metadata = metadata_service.build_video_metadata(
                project, result.output_path.name, seed, backend.name, backend.is_mock,
                result.actual_format, result.duration_seconds, model_bundle=job.model_bundle,
                unsupported_params=result.unsupported_params,
                report=result.report, preset_notes=result.preset_notes,
                sampler_backend=result.sampler_backend,
                model_sampling=result.model_sampling,
                vram_cleanup=vram_cleanup,
            )
            meta_name = metadata_service.write_video_metadata(pdir, result.output_path.name, metadata)

            entry = GeneratedVideoEntry(
                filename=result.output_path.name,
                preview=result.preview_path.name if result.preview_path else None,
                metadata_file=meta_name,
                mode=project.generation_mode.value,
                model_id=project.model_id,
                model_bundle_id=(job.model_bundle or {}).get("model_bundle_id", project.model_id),
                model_bundle_name=(job.model_bundle or {}).get("model_bundle_name", ""),
                model_bundle_snapshot=job.model_bundle,
                resolution=project.resolution.label(),
                fps=project.params.fps,
                frames=project.params.frames,
                seed=seed,
                is_mock=backend.is_mock,
            )
            # Re-load before saving so we do not clobber edits made during generation.
            fresh = project_service.load_project(job.project_id)
            fresh.generated_videos.append(entry)
            next_seed_note = self._apply_control_after_generate(fresh, seed)
            if next_seed_note:
                self._log(job_id, next_seed_note)
            project_service.save_project(fresh)

            self._set(
                job_id,
                status=JobStatus.COMPLETED,
                progress=100,
                stage="Completed",
                finished_at=utc_now(),
                output_file=result.output_path.name,
                output_url=f"/media/projects/{project.id}/outputs/{result.output_path.name}",
                preview_url=f"/media/projects/{project.id}/previews/{result.preview_path.name}" if result.preview_path else None,
                metadata_file=meta_name,
            )
            self._log(job_id, f"Output saved: {result.output_path.name}")
            logger.info("Generation completed: job %s -> %s", job_id, result.output_path.name)
        except GenerationCancelled:
            self._handle_cancellation(job_id, project, pdir, output_dir, preview_dir,
                                      stem, backend.name, utc_now())
        except (GenerationError, project_service.ProjectError) as exc:
            self._set(job_id, status=JobStatus.FAILED, stage="Failed", error=str(exc), finished_at=utc_now())
            self._log(job_id, f"ERROR: {exc}")
            logger.error("Generation failed: job %s: %s", job_id, exc)
            # A failed render must still attempt VRAM cleanup (patch8 §15). The
            # cleanup never hides or replaces the original generation error.
            self._cleanup_after_failure(job_id)
        except Exception as exc:  # noqa: BLE001 — job thread must never crash silently
            self._set(job_id, status=JobStatus.FAILED, stage="Failed",
                      error=f"Unexpected error: {exc}", finished_at=utc_now())
            self._log(job_id, f"UNEXPECTED ERROR: {exc}")
            logger.exception("Generation crashed: job %s", job_id)
            self._cleanup_after_failure(job_id)
        finally:
            self._slots.release()
            with self._lock:
                self._cancel_flags.pop(job_id, None)

    def _cleanup_after_failure(self, job_id: str) -> None:
        """VRAM cleanup on the failure paths (patch8 §15). Guarded so a cleanup
        error can never mask the original failure."""
        if not settings.wan_clear_temp_vram_after_generation:
            return
        try:
            self._run_vram_cleanup(job_id, f"job_failed:{job_id}",
                                   settings.wan_unload_model_after_generation)
        except Exception as exc:  # noqa: BLE001 — cleanup must not raise here
            logger.warning("VRAM cleanup after failure raised (ignored): %s", exc)

    def _handle_cancellation(self, job_id, project, pdir, output_dir, preview_dir,
                             stem, backend_name, when) -> None:
        """Mark a job cancelled and clean up its partial output safely — never
        touches previously completed videos, sources, models or project files
        (patch6b §10/§14)."""
        deleted = self._cleanup_partial(output_dir, preview_dir, stem)
        # Mandatory VRAM cleanup after Stop/Cancel (patch8 §16). Runs even though
        # backend cancellation may only take effect after the active inference
        # call returns — when control reaches here, cleanup still runs.
        cancel_cleanup = None
        if settings.wan_clear_temp_vram_after_cancel:
            cancel_cleanup = self._run_vram_cleanup(
                job_id, f"job_cancelled:{job_id}",
                settings.wan_unload_model_after_cancel)
        else:
            # Even with cleanup disabled, release transient VRAM (best effort).
            try:
                wan_backend.release_generation_memory()
            except Exception:  # noqa: BLE001 — cleanup must never raise
                pass
        # Persist a lightweight cancellation record (patch6b §11). It is NOT
        # added to the project's generated_videos list.
        try:
            meta = metadata_service.write_cancellation_metadata(pdir, stem, {
                "job_id": job_id,
                "project_id": project.id,
                "project_name": project.name,
                "status": "cancelled",
                "backend": backend_name,
                "cancelled_at": when,
                "partial_output_deleted": bool(deleted),
                "deleted_files": deleted,
                "vram_cleanup": {"after_cancel": cancel_cleanup} if cancel_cleanup else None,
                "message": "Generation cancelled by user.",
            })
        except Exception as exc:  # noqa: BLE001 — metadata write must not break cancel
            meta = None
            logger.warning("Could not write cancellation metadata for job %s: %s", job_id, exc)
        if deleted:
            self._log(job_id, "Partial output removed: " + ", ".join(deleted))
        self._set(job_id, status=JobStatus.CANCELLED, stage="Cancelled",
                  progress=0, cancelled_at=when, finished_at=when,
                  error=None, metadata_file=meta)
        self._log(job_id, "Generation cancelled.")
        logger.info("Generation cancelled: job %s (partial files removed: %d)",
                    job_id, len(deleted))

    def _run_vram_cleanup(self, job_id: str, reason: str, unload_models: bool) -> dict:
        """Centralized VRAM cleanup for a job exit path (patch8 §13/§14/§16).
        Never raises; logs and records the report on the job."""
        report = gpu_memory_service.cleanup_vram(reason=reason, unload_models=unload_models)
        self._log(job_id, gpu_memory_service.summarize(report))
        for err in report.get("errors", []):
            self._log(job_id, f"VRAM cleanup warning: {err}")
        self._set(job_id, last_vram_cleanup=report)
        return report

    @staticmethod
    def _cleanup_partial(output_dir: Path, preview_dir: Path, stem: str) -> list[str]:
        """Delete only THIS job's partial output/preview files (matched by its
        unique output stem). Never deletes any other file."""
        removed: list[str] = []
        for directory in (output_dir, preview_dir):
            try:
                for path in directory.glob(f"{stem}.*"):
                    try:
                        path.unlink()
                        removed.append(path.name)
                    except OSError as exc:
                        logger.warning("Could not delete partial file %s: %s", path, exc)
            except OSError:
                continue
        return removed


job_manager = JobManager()
