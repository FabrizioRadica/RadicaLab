"""ComfyUI-compatible workflow export.

Produces a JSON file with a ComfyUI-style node graph for a Wan2.2 pipeline plus
a rich `extra.wan_video_generator` metadata block containing every project and
generation setting.

Compatibility note (also embedded in the export): exact node compatibility
depends on the Wan video node packages installed in the local ComfyUI. The
graph uses common Wan/video node class names and may need to be re-linked to
the node variants available in the user's installation.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from app.config import logger, settings
from app.models.project_models import ExportedWorkflowEntry, Project
from app.services import project_service

COMPATIBILITY_NOTE = (
    "This workflow targets a Wan2.2 video pipeline. Exact node compatibility depends on the "
    "Wan video node packages installed in your ComfyUI (e.g. WanVideo wrapper nodes). "
    "If a node class is missing, re-link the equivalent node from your installation. "
    "All generation settings are also available under extra.wan_video_generator."
)


class WorkflowExportError(Exception):
    """Raised when workflow export fails with a user-readable message."""


def _node(node_id: int, class_type: str, title: str, inputs: dict) -> tuple[str, dict]:
    return str(node_id), {"class_type": class_type, "_meta": {"title": title}, "inputs": inputs}


def _resolve_model_sampling(project: Project) -> tuple[dict, list[str]]:
    """Preset-aware ModelSamplingSD3 settings for the export, matching what the
    render path would apply (patch7 §12/§15). Imported lazily to avoid a circular
    import with generation_service."""
    try:
        from app.services.generation_service import resolve_model_sampling

        return resolve_model_sampling(project)
    except Exception:  # noqa: BLE001 — export must still work if resolution fails
        ms = project.params.model_sampling
        return {"enabled": bool(ms.enabled), "type": ms.type, "shift": float(ms.shift)}, []


def build_workflow(project: Project, seed_used: int | None = None) -> dict:
    is_i2v = project.generation_mode.value == "image2video"
    seed = seed_used if seed_used is not None else project.params.seed
    composed_prompt = project.composed_prompt()

    # Resolve the full model bundle so the export carries every component path.
    try:
        from app.services import model_service

        bundle = model_service.get_model(project.model_id).component_snapshot()
    except Exception:  # noqa: BLE001 — model may have been removed; export still works
        bundle = {"model_bundle_id": project.model_id, "model_bundle_name": project.model_id}

    nodes: dict[str, dict] = {}

    nid, node = _node(1, "WanVideoModelLoader", "Load Wan Model", {
        "model_name": project.model_id,
        "diffusion_model": bundle.get("diffusion_model_path", ""),
        "checkpoint": bundle.get("checkpoint_path", ""),
        "vae": bundle.get("vae_path", ""),
        "text_encoder": bundle.get("text_encoder_path", ""),
        "clip": bundle.get("clip_path", ""),
        "t5_encoder": bundle.get("t5_encoder_path", ""),
        "vision_encoder": bundle.get("vision_encoder_path", ""),
        "tokenizer": bundle.get("tokenizer_path", ""),
        "config": bundle.get("config_path", ""),
        "scheduler_config": bundle.get("scheduler_config_path", ""),
        "precision": project.params.advanced.precision,
        "load_device": project.params.advanced.device,
    })
    nodes[nid] = node

    nid, node = _node(2, "CLIPTextEncode", "Positive Prompt", {
        "text": composed_prompt,
        "clip": ["1", 1],
    })
    nodes[nid] = node

    nid, node = _node(3, "CLIPTextEncode", "Negative Prompt", {
        "text": project.negative_prompt,
        "clip": ["1", 1],
    })
    nodes[nid] = node

    next_id = 4
    latent_input: list = []
    if is_i2v:
        nodes[str(next_id)] = {
            "class_type": "LoadImage",
            "_meta": {"title": "Source Image"},
            "inputs": {"image": project.source_image or ""},
        }
        image_node = next_id
        next_id += 1
        nodes[str(next_id)] = {
            "class_type": "WanVideoImageToVideoEncode",
            "_meta": {"title": "Image2Video Encode"},
            "inputs": {
                "image": [str(image_node), 0],
                "width": project.resolution.width,
                "height": project.resolution.height,
                "num_frames": project.params.frames,
                "image_influence": project.params.image_influence,
            },
        }
        latent_input = [str(next_id), 0]
        next_id += 1
    else:
        nodes[str(next_id)] = {
            "class_type": "WanVideoEmptyEmbeds",
            "_meta": {"title": "Empty Video Latent"},
            "inputs": {
                "width": project.resolution.width,
                "height": project.resolution.height,
                "num_frames": project.params.frames,
            },
        }
        latent_input = [str(next_id), 0]
        next_id += 1

    # patchoptimization §11 — detected model speed profile so the export
    # reflects why Turbo/Lightning models use low CFG / low steps intentionally.
    try:
        from app.services import preset_service

        profile_meta = preset_service.generation_profile_metadata(project, bundle)
    except Exception:  # noqa: BLE001 — export must still work if detection fails
        profile_meta = None

    # patch7 §12 — ModelSamplingSD3 stage between the model loader and the
    # sampler. When enabled, the sampler must receive the modified model output,
    # NOT the original loaded diffusion model.
    ms_settings, _ms_notes = _resolve_model_sampling(project)
    model_ref: list = ["1", 0]
    if ms_settings["enabled"]:
        ms_id = next_id
        nodes[str(ms_id)] = {
            "class_type": "ModelSamplingSD3",
            "_meta": {"title": "ModelSamplingSD3"},
            "inputs": {
                "model": ["1", 0],
                "shift": ms_settings["shift"],
            },
        }
        model_ref = [str(ms_id), 0]
        next_id += 1

    sampler_id = next_id
    nodes[str(sampler_id)] = {
        "class_type": "WanVideoSampler",
        "_meta": {"title": "Wan Video Sampler"},
        # KSampler-compatible inputs: guidance_scale is exported as `cfg`;
        # denoise is its own input and is never mapped to cfg/motion/influence.
        # `control_after_generate` is a ComfyUI widget behavior, not a node
        # input — it is exported in extra.wan_video_generator.sampling instead
        # of inventing a fake node input. `model` comes from ModelSamplingSD3
        # when enabled (patch7), else directly from the loader.
        "inputs": {
            "model": model_ref,
            "positive": ["2", 0],
            "negative": ["3", 0],
            "latents": latent_input,
            "seed": seed,
            "steps": project.params.advanced.steps,
            "cfg": project.params.guidance_scale,
            "sampler_name": project.params.sampler_name,
            "scheduler": project.params.scheduler,
            "denoise": project.params.denoise,
            "motion_strength": project.params.motion_strength,
        },
    }
    next_id += 1

    decode_id = next_id
    nodes[str(decode_id)] = {
        "class_type": "WanVideoDecode",
        "_meta": {"title": "VAE Decode"},
        "inputs": {"samples": [str(sampler_id), 0], "vae": ["1", 2]},
    }
    next_id += 1

    if bundle.get("lora_paths"):
        nodes[str(next_id)] = {
            "class_type": "WanVideoLoraSelect",
            "_meta": {"title": "LoRA Stack"},
            "inputs": {
                "model": ["1", 0],
                "lora_paths": list(bundle.get("lora_paths", [])),
                "strength": 1.0,
            },
        }
        next_id += 1

    # patchoptimization §11 — a standalone Note node documenting the detected
    # accelerated profile (low CFG/steps are intentional, not a mistake).
    if profile_meta and profile_meta.get("detected_model_profile") in (
            "turbo", "lightning", "lightx2v", "fast"):
        nodes[str(next_id)] = {
            "class_type": "Note",
            "_meta": {"title": "Wan Turbo/Lightning note"},
            "inputs": {"text":
                       "Turbo/Lightning-style Wan model detected. Low CFG and low "
                       "steps are intentional to avoid flicker/color flashes."},
        }
        next_id += 1

    if settings.comfyui_default_save_node:
        nodes[str(next_id)] = {
            "class_type": "VHS_VideoCombine",
            "_meta": {"title": "Save Video"},
            "inputs": {
                "images": [str(decode_id), 0],
                "frame_rate": project.params.fps,
                "format": f"video/{project.params.output_format}",
                "filename_prefix": project_service.sanitize_folder_name(project.name),
                "save_output": True,
            },
        }

    cm = project.camera_motion
    return {
        "workflow_format": settings.comfyui_workflow_version,
        "nodes": nodes,
        "extra": {
            "wan_video_generator": {
                "application": settings.app_name,
                "app_version": settings.app_version,
                "credits": {
                    "concept_and_design": settings.credits_concept,
                    "project_by": settings.credits_project,
                },
                "compatibility_note": COMPATIBILITY_NOTE,
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "project": {
                    "id": project.id,
                    "name": project.name,
                    "description": project.description,
                    "tags": project.tags,
                    "created_at": project.created_at,
                },
                "model_bundle": bundle,
                "generation": {
                    "mode": project.generation_mode.value,
                    "model_id": project.model_id,
                    "positive_prompt": project.positive_prompt,
                    "negative_prompt": project.negative_prompt,
                    "final_composed_prompt": composed_prompt,
                    "resolution": project.resolution.label(),
                    "orientation": project.orientation.value,
                    "fps": project.params.fps,
                    "frames": project.params.frames,
                    "requested_duration_seconds": project.duration_seconds(),
                    "seed": seed,
                    "random_seed": project.params.random_seed,
                    "guidance_scale": project.params.guidance_scale,
                    # KSampler-compatible sampling block. Both namings are kept:
                    # guidance_scale (generic backend) and cfg (ComfyUI/KSampler).
                    "sampling": {
                        "seed": seed,
                        "control_after_generate": project.params.control_after_generate,
                        "steps": project.params.advanced.steps,
                        "cfg": project.params.guidance_scale,
                        "guidance_scale": project.params.guidance_scale,
                        "sampler_name": project.params.sampler_name,
                        "scheduler": project.params.scheduler,
                        "denoise": project.params.denoise,
                        "note": "control_after_generate is a ComfyUI seed-widget "
                                "behavior and is intentionally not a sampler node "
                                "input. Sampler/scheduler support depends on the "
                                "installed Wan node pack — re-link if needed.",
                    },
                    # ModelSamplingSD3 modifier (patch7 §12). Reflects the node
                    # inserted between the loader and the sampler above.
                    "model_sampling": {
                        "enabled": ms_settings["enabled"],
                        "type": ms_settings["type"] if ms_settings["enabled"] else None,
                        "shift": ms_settings["shift"] if ms_settings["enabled"] else None,
                        "node": "ModelSamplingSD3" if ms_settings["enabled"] else None,
                        "note": "When enabled, the graph is Load Diffusion Model → "
                                "ModelSamplingSD3(shift) → Sampler; the sampler receives "
                                "the modified model, not the raw loaded model.",
                    },
                    # Detected model speed profile + turbo warning
                    # (patchoptimization §11). Low CFG/steps on Turbo models are
                    # intentional and must not be "corrected" to normal values.
                    "model_profile": profile_meta,
                    "motion_strength": project.params.motion_strength,
                    "image_influence": project.params.image_influence if is_i2v else None,
                    "output_format": project.params.output_format,
                    "source_image": project.source_image if is_i2v else None,
                    "advanced": project.params.advanced.model_dump(mode="json"),
                },
                "camera_motion": {
                    "enabled": cm.enabled,
                    "movement_type": cm.movement_type.value,
                    "fragment": cm.fragment,
                    "applied_to_prompt": cm.applied_to_prompt,
                    "settings": cm.model_dump(mode="json"),
                },
            }
        },
    }


def compare_with_comfyui(project_id: str) -> dict:
    """Parity comparison tool (patch6 §18).

    Saves a diagnostic JSON comparing the user's requested settings, the direct
    Wan backend's effective settings, and the exported ComfyUI workflow settings,
    listing every configuration mismatch. This is the first parity target:
    detecting configuration differences, not image similarity.
    """
    from app.services import metadata_service, wan_backend
    from app.services.generation_service import apply_quality_preset

    project = project_service.load_project(project_id)
    seed = project.params.seed
    try:
        from app.services import model_service

        bundle = model_service.get_model(project.model_id).component_snapshot()
    except Exception:  # noqa: BLE001 — comparison must still work without the model
        bundle = {"model_bundle_id": project.model_id, "model_bundle_name": project.model_id}

    requested = metadata_service._requested_settings(project, seed, bundle)

    # Predicted effective settings of the direct diffusers backend (patch6c §7):
    # the requested sampler is used exactly when the direct backend supports it,
    # otherwise the sampler fallback policy decides (route / block / fallback).
    # Denoise stays full; compute is WAN_TORCH_DTYPE; presets adjust steps/offload.
    from app.services import sampler_registry
    from app.services.generation_service import resolve_sampling_plan

    effective_steps, offload_mode, preset_notes = apply_quality_preset(project)
    plan = resolve_sampling_plan(project)
    if plan.effective_sampler and not plan.blocked:
        eff_sampler = plan.effective_sampler
        eff_scheduler = "Wan flow-matching sigma schedule"
    else:
        eff_sampler = plan.requested_sampler + " (blocked)"
        eff_scheduler = "n/a"
    effective = dict(requested)
    effective.update({
        "sampler_name": eff_sampler,
        "scheduler": eff_scheduler,
        "denoise": 1.0,
        "steps": effective_steps,
        "precision": wan_backend.effective_compute_precision()
        if not wan_backend.missing_dependencies() else settings.wan_torch_dtype,
        "offload_policy": settings._OFFLOAD_MODE_TO_POLICY.get(offload_mode, offload_mode),
        "effective_render_backend": plan.effective_backend,
    })

    # ComfyUI workflow settings mirror the requested values (the export is
    # faithful to the UI), so the effective direct-backend column is what the
    # user must reconcile for parity.
    workflow = build_workflow(project, seed_used=seed)
    comfy_sampling = (workflow.get("extra", {}).get("wan_video_generator", {})
                      .get("generation", {}).get("sampling", {}))

    compared_fields = ("sampler_name", "scheduler", "denoise", "steps", "cfg",
                       "precision", "offload_policy", "resolution", "frames", "fps")
    mismatches = []
    for field_name in compared_fields:
        rv = requested.get(field_name)
        ev = effective.get(field_name)
        if rv is not None and ev is not None and str(rv) != str(ev):
            mismatches.append({
                "field": field_name,
                "requested": rv,
                "effective_direct_backend": ev,
                "comfyui_workflow": comfy_sampling.get(field_name, rv),
            })

    result = {
        "application": settings.app_name,
        "project_id": project.id,
        "project_name": project.name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "requested_settings": requested,
        "effective_direct_backend_settings": effective,
        "comfyui_workflow_sampling": comfy_sampling,
        "preset_notes": preset_notes,
        "mismatches": mismatches,
        "note": "Mismatches list settings the direct diffusers backend cannot apply "
                "exactly. For guaranteed parity, render through the ComfyUI API backend "
                "(COMFYUI_API_ENABLED) or match these settings in ComfyUI.",
    }

    pdir = project_service.project_dir(project.folder)
    cmp_dir = pdir / "diagnostics"
    cmp_dir.mkdir(exist_ok=True)
    base = project_service.sanitize_folder_name(project.name)
    index = 1
    filename = f"{base}_comfyui_compare_{index:04d}.json"
    while (cmp_dir / filename).exists():
        index += 1
        filename = f"{base}_comfyui_compare_{index:04d}.json"
    (cmp_dir / filename).write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("ComfyUI parity comparison saved for '%s': %s (%d mismatch(es))",
                project.name, filename, len(mismatches))
    result["filename"] = filename
    return result


def export_workflow(project_id: str) -> dict:
    if not settings.comfyui_export_enabled:
        raise WorkflowExportError("ComfyUI export is disabled in the configuration (.env).")
    project = project_service.load_project(project_id)
    try:
        workflow = build_workflow(project)
        pdir = project_service.project_dir(project.folder)
        wf_dir = pdir / "workflows"
        wf_dir.mkdir(exist_ok=True)
        index = len(project.exported_workflows) + 1
        base = project_service.sanitize_folder_name(project.name)
        filename = f"{base}_comfyui_{index:04d}.json"
        while (wf_dir / filename).exists():
            index += 1
            filename = f"{base}_comfyui_{index:04d}.json"
        payload = json.dumps(workflow, indent=2, ensure_ascii=False)
        (wf_dir / filename).write_text(payload, encoding="utf-8")

        # Also keep a copy in the global workflows root for easy browsing.
        settings.workflows_root.mkdir(parents=True, exist_ok=True)
        (settings.workflows_root / filename).write_text(payload, encoding="utf-8")

        entry = ExportedWorkflowEntry(filename=filename, path=str(wf_dir / filename))
        project.exported_workflows.append(entry)
        project_service.save_project(project)
        logger.info("Workflow exported for project '%s': %s", project.name, filename)
        return {"filename": filename, "path": str(wf_dir / filename), "url": f"/media/projects/{project.id}/workflows/{filename}"}
    except WorkflowExportError:
        raise
    except OSError as exc:
        logger.error("Workflow export failed for project %s: %s", project_id, exc)
        raise WorkflowExportError(f"Could not write the workflow file: {exc}") from exc
