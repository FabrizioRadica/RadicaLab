"""Wan2.2 TI2V 5B practical presets + Turbo/Lightning awareness.

patch8 introduced three normal presets (Fast Preview / Safe Quality / Low VRAM).
patchoptimization adds:

* model *speed profile* detection (normal vs turbo/lightning/lightx2v/fast) from
  the selected Model Bundle's names / paths / LoRAs (§2);
* explicit preset *families* — Normal and Turbo/Lightning — with orientation
  variants and model-appropriate CFG / step counts (§3-§5);
* automatic ModelSamplingSD3 shift by generation mode (T2V 8.0 / I2V 5.0, §6);
* a warning system for dangerous model/preset combinations (§7);
* profile metadata for generation, diagnostics and ComfyUI export (§9-§11).

A preset never renders anything itself: it computes concrete, Wan-valid target
values that the UI writes into the real project fields, so every preset value
affects the actual generation request (patchoptimization §0/§13). No accelerator
(SageAttention/TeaCache) is added.
"""

from __future__ import annotations

from app.config import settings

MANUAL = "manual"

# --- Legacy patch8 preset ids (kept working — stored in existing projects) ----
FAST_PREVIEW = "wan22_5b_fast_preview"
SAFE_QUALITY = "wan22_5b_safe_quality"
LOW_VRAM = "wan22_5b_low_vram"
_LEGACY_IDS = (FAST_PREVIEW, SAFE_QUALITY, LOW_VRAM)

# --- Model speed profiles (patchoptimization §2) ------------------------------
PROFILE_NORMAL = "normal"
PROFILE_TURBO = "turbo"
PROFILE_LIGHTNING = "lightning"
PROFILE_LIGHTX2V = "lightx2v"
PROFILE_FAST = "fast"
PROFILE_UNKNOWN = "unknown"

# Accelerated/distilled profiles that require low CFG + low step count. Using
# normal Wan2.2 presets on these causes flicker / colored flashes / overexposure.
ACCELERATED_PROFILES = (PROFILE_TURBO, PROFILE_LIGHTNING, PROFILE_LIGHTX2V, PROFILE_FAST)


def detect_wan_speed_profile(bundle: dict) -> str:
    """Detect the model speed profile from a Model Bundle component snapshot
    (patchoptimization §2). Looks at every name/path/LoRA — turbo/lightning/
    lightx2v-style models must not be driven with normal Wan2.2 settings."""
    bundle = bundle or {}
    values: list[str] = []
    for key in (
        "model_bundle_name",
        "model_bundle_id",
        "checkpoint_path",
        "diffusion_model_path",
        "text_encoder_path",
        "vae_path",
    ):
        value = bundle.get(key)
        if value:
            values.append(str(value).lower())

    custom = bundle.get("custom_component_paths") or {}
    for value in custom.values():
        if value:
            values.append(str(value).lower())

    for value in bundle.get("lora_paths") or []:
        if value:
            values.append(str(value).lower())

    joined = " ".join(values)

    if "lightx2v" in joined or "lightx" in joined:
        return PROFILE_LIGHTX2V
    if "lightning" in joined:
        return PROFILE_LIGHTNING
    if "turbo" in joined:
        return PROFILE_TURBO
    if "fast" in joined:
        return PROFILE_FAST
    return PROFILE_NORMAL


def detect_profile_for_project(project) -> str:
    """Speed profile of the project's currently selected model bundle. Returns
    'unknown' when the model can't be resolved (never raises)."""
    try:
        from app.services import model_service

        bundle = model_service.get_model(project.model_id).component_snapshot()
    except Exception:  # noqa: BLE001 — detection must never break preset/metadata flows
        return PROFILE_UNKNOWN
    return detect_wan_speed_profile(bundle)


def resolve_preset_shift(preset: dict, mode: str) -> float:
    """ModelSamplingSD3 shift for the current generation mode (patchoptimization
    §6): I2V 5.0, T2V 8.0. Never hard-codes 8.0 for Image2Video."""
    if (mode or "").lower() == "image2video":
        return float(preset.get("shift_i2v", preset.get("model_sampling_shift_i2v", 8.0)))
    return float(preset.get("shift_t2v", preset.get("model_sampling_shift_t2v", 8.0)))


def _round_to_multiple(value: int, multiple: int) -> int:
    multiple = max(int(multiple or 16), 1)
    return max(multiple, int(round(value / multiple)) * multiple)


def _res_multiple() -> int:
    return settings.wan_validate_resolution_multiple_of or 16


# ==========================================================================
# Legacy patch8 presets (values still driven by .env so existing behavior is
# unchanged). profile is always "normal".
# ==========================================================================
_LEGACY_PROFILES = {
    FAST_PREVIEW: {
        "label": "Wan2.2 5B Fast Preview",
        "description": "Fast prompt/movement testing. Preview quality, not final.",
        "steps_key": "wan_fast_preview_steps", "frames_key": "wan_fast_preview_frames",
        "cfg": 3.5, "step_range": (16, 20), "cfg_range": (3.5, 4.0),
        "offload_policy": "balanced", "unload_after_generation": False,
        "warning": "Fast Preview is for testing — use Safe Quality for final renders.",
    },
    SAFE_QUALITY: {
        "label": "Wan2.2 5B Safe Quality",
        "description": "Recommended default profile for Wan2.2 TI2V 5B.",
        "steps_key": "wan_safe_quality_steps", "frames_key": "wan_safe_quality_frames",
        "cfg": 4.0, "step_range": (30, 40), "cfg_range": (3.5, 4.5),
        "offload_policy": "balanced", "unload_after_generation": False,
        "warning": None,
    },
    LOW_VRAM: {
        "label": "Wan2.2 5B Low VRAM",
        "description": "For machines around or below 8 GB VRAM.",
        "steps_key": "wan_low_vram_steps", "frames_key": "wan_low_vram_frames",
        "cfg": 3.5, "step_range": (20, 24), "cfg_range": (3.5, 4.0),
        "offload_policy": "aggressive", "unload_after_generation": True,
        "warning": "Low VRAM preset reduces resolution/frame count to keep "
                   "generation stable.",
    },
}


def _legacy_resolution(preset_id: str, orientation: str) -> tuple[int, int]:
    orientation = (orientation or "landscape").lower()
    landscape = (640, 352) if preset_id == LOW_VRAM else (832, 480)
    w, h = landscape
    mult = _res_multiple()
    if orientation == "portrait":
        w, h = h, w
    elif orientation == "square":
        s = _round_to_multiple(min(w, h), mult)
        w, h = s, s
    return _round_to_multiple(w, mult), _round_to_multiple(h, mult)


# ==========================================================================
# patchoptimization presets (§4 Normal, §5 Turbo/Lightning). Fully explicit —
# every value is a real parameter written to the project. Turbo presets use low
# CFG (1.0-1.2) and low steps (4-6) to avoid flicker/color flashes.
# ==========================================================================
def _p(label, description, profile, orientation, resolution, frames, steps, cfg,
       step_range, cfg_range, warning=None, by_orientation=False):
    return {
        "label": label, "description": description, "profile": profile,
        "orientation": orientation, "resolution": resolution, "by_orientation": by_orientation,
        "frames": frames, "fps": 16, "steps": steps, "cfg": cfg,
        "sampler": "euler", "scheduler": "simple", "denoise": 1.0,
        "shift_t2v": 8.0, "shift_i2v": 8.0,
        "memory_optimization": True, "model_offload": False, "unload_after_generation": False,
        "step_range": step_range, "cfg_range": cfg_range, "warning": warning,
    }


_HEAVY_WARNING = ("Heavy render preset. This may be much slower and use more VRAM. "
                  "For tests, use Fast Preview first.")

_PRESETS_V2: dict[str, dict] = {
    # --- Normal Wan2.2 5B (§4) ------------------------------------------------
    "wan22_5b_fast_preview_landscape": _p(
        "Wan2.2 5B Fast Preview — Landscape",
        "Fast landscape preview on a normal Wan2.2 5B model.",
        PROFILE_NORMAL, "landscape", (640, 352), 49, 12, 3.0, (8, 16), (3.0, 4.5)),
    "wan22_5b_fast_preview_portrait": _p(
        "Wan2.2 5B Fast Preview — Portrait",
        "Fast portrait preview on a normal Wan2.2 5B model.",
        PROFILE_NORMAL, "portrait", (352, 640), 49, 12, 3.0, (8, 16), (3.0, 4.5)),
    "wan22_5b_safe_quality_landscape": _p(
        "Wan2.2 5B Safe Quality — Landscape",
        "Balanced landscape quality on a normal Wan2.2 5B model.",
        PROFILE_NORMAL, "landscape", (832, 480), 81, 24, 3.5, (18, 32), (3.0, 4.5)),
    "wan22_5b_safe_quality_portrait": _p(
        "Wan2.2 5B Safe Quality — Portrait",
        "Balanced portrait quality on a normal Wan2.2 5B model.",
        PROFILE_NORMAL, "portrait", (480, 832), 81, 24, 3.5, (18, 32), (3.0, 4.5)),
    "wan22_5b_high_quality_landscape": _p(
        "Wan2.2 5B High Quality — Landscape",
        "High landscape quality — heavy render.",
        PROFILE_NORMAL, "landscape", (1280, 704), 81, 32, 4.0, (24, 40), (3.5, 4.5),
        warning=_HEAVY_WARNING),
    "wan22_5b_high_quality_portrait": _p(
        "Wan2.2 5B High Quality — Portrait",
        "High portrait quality — heavy render.",
        PROFILE_NORMAL, "portrait", (704, 1280), 81, 32, 4.0, (24, 40), (3.5, 4.5),
        warning=_HEAVY_WARNING),
    # --- Turbo / Lightning / Lightx2v (§5) ------------------------------------
    "wan22_5b_turbo_preview_landscape": _p(
        "Wan2.2 5B Turbo Preview — Landscape",
        "Accelerated landscape preview — low CFG / low steps.",
        PROFILE_TURBO, "landscape", (640, 352), 49, 4, 1.0, (4, 6), (1.0, 1.2)),
    "wan22_5b_turbo_preview_portrait": _p(
        "Wan2.2 5B Turbo Preview — Portrait",
        "Accelerated portrait preview — low CFG / low steps.",
        PROFILE_TURBO, "portrait", (352, 640), 49, 4, 1.0, (4, 6), (1.0, 1.2)),
    "wan22_5b_turbo_quality_landscape": _p(
        "Wan2.2 5B Turbo Quality — Landscape",
        "Accelerated landscape quality — low CFG / low steps.",
        PROFILE_TURBO, "landscape", (832, 480), 49, 5, 1.0, (4, 6), (1.0, 1.2)),
    "wan22_5b_turbo_quality_portrait": _p(
        "Wan2.2 5B Turbo Quality — Portrait",
        "Accelerated portrait quality — low CFG / low steps.",
        PROFILE_TURBO, "portrait", (480, 832), 49, 5, 1.0, (4, 6), (1.0, 1.2)),
    "wan22_5b_turbo_experimental_quality": _p(
        "Wan2.2 5B Turbo Experimental Quality",
        "Accelerated, more frames — experimental.",
        PROFILE_TURBO, None, None, 73, 6, 1.2, (4, 6), (1.0, 1.2),
        warning="Experimental. If flicker appears, return to CFG 1.0 and 4-5 steps.",
        by_orientation=True),
}

# Selector order (patchoptimization §3/§8): Manual, normal family, turbo family,
# then the legacy patch8 presets so old projects still find their preset.
PRESET_ORDER = (
    MANUAL,
    "wan22_5b_fast_preview_landscape", "wan22_5b_fast_preview_portrait",
    "wan22_5b_safe_quality_landscape", "wan22_5b_safe_quality_portrait",
    "wan22_5b_high_quality_landscape", "wan22_5b_high_quality_portrait",
    "wan22_5b_turbo_preview_landscape", "wan22_5b_turbo_preview_portrait",
    "wan22_5b_turbo_quality_landscape", "wan22_5b_turbo_quality_portrait",
    "wan22_5b_turbo_experimental_quality",
    FAST_PREVIEW, SAFE_QUALITY, LOW_VRAM,
)
_PRESET_IDS = set(PRESET_ORDER)

_GROUP_LABELS = {
    PROFILE_NORMAL: "Normal presets",
    PROFILE_TURBO: "Turbo / Lightning presets",
    "manual": "Manual",
}


def normalize_preset(preset_id: str) -> str:
    pid = (preset_id or "").strip().lower()
    return pid if pid in _PRESET_IDS else MANUAL


def preset_profile_of(preset_id: str) -> str:
    """Family profile of a preset: 'manual', 'normal' or 'turbo'
    (patchoptimization §3/§10)."""
    pid = normalize_preset(preset_id)
    if pid == MANUAL:
        return "manual"
    if pid in _PRESETS_V2:
        return _PRESETS_V2[pid]["profile"]
    return PROFILE_NORMAL  # legacy presets are normal


def _v2_resolution(entry: dict, orientation: str) -> tuple[int, int]:
    mult = _res_multiple()
    if entry.get("by_orientation"):
        w, h = (480, 832) if (orientation or "").lower() == "portrait" else (832, 480)
    else:
        w, h = entry["resolution"]
    return _round_to_multiple(w, mult), _round_to_multiple(h, mult)


def _v2_orientation(entry: dict, orientation: str) -> str:
    if entry.get("orientation"):
        return entry["orientation"]
    return (orientation or "landscape").lower()


def preset_definitions() -> list[dict]:
    """Static preset catalogue for the UI selector, grouped by family
    (patchoptimization §3/§8). Legacy patch8 presets are included so existing
    projects keep a valid selection."""
    defs = [{"id": MANUAL, "label": "Manual", "manual": True, "profile": "manual",
             "group": _GROUP_LABELS["manual"], "orientation": None,
             "description": "Your own settings — no preset changes are applied."}]

    for pid in PRESET_ORDER:
        if pid == MANUAL:
            continue
        if pid in _PRESETS_V2:
            e = _PRESETS_V2[pid]
            defs.append({
                "id": pid, "label": e["label"], "manual": False,
                "profile": e["profile"], "group": _GROUP_LABELS.get(e["profile"], "Presets"),
                "orientation": e["orientation"], "description": e["description"],
                "steps": e["steps"], "frames": e["frames"], "fps": e["fps"],
                "sampler_name": e["sampler"], "scheduler": e["scheduler"], "cfg": e["cfg"],
                "step_range": list(e["step_range"]), "cfg_range": list(e["cfg_range"]),
                "model_sampling_shift_t2v": e["shift_t2v"],
                "model_sampling_shift_i2v": e["shift_i2v"],
                "warning": e["warning"],
            })
        elif pid in _LEGACY_PROFILES:
            p = _LEGACY_PROFILES[pid]
            defs.append({
                "id": pid, "label": p["label"] + " (legacy)", "manual": False,
                "profile": PROFILE_NORMAL, "group": _GROUP_LABELS[PROFILE_NORMAL],
                "orientation": None, "description": p["description"],
                "steps": getattr(settings, p["steps_key"]),
                "frames": getattr(settings, p["frames_key"]),
                "fps": settings.wan_default_fps,
                "sampler_name": "euler", "scheduler": "simple",
                "cfg": p["cfg"], "step_range": list(p["step_range"]),
                "cfg_range": list(p["cfg_range"]),
                "model_sampling_shift_t2v": 8.0, "model_sampling_shift_i2v": 5.0,
                "warning": p["warning"], "legacy": True,
            })
    return defs


def compute_preset(preset_id: str, mode: str, orientation: str) -> dict | None:
    """Concrete target parameter values for a preset. None for Manual (§3).

    The result is what the UI writes into the real form/project fields, so every
    value here affects the actual generation request."""
    pid = normalize_preset(preset_id)
    if pid == MANUAL:
        return None

    if pid in _PRESETS_V2:
        e = _PRESETS_V2[pid]
        w, h = _v2_resolution(e, orientation)
        return {
            "wan_preset": pid,
            "profile": e["profile"],
            "orientation": _v2_orientation(e, orientation),
            "resolution": {"width": w, "height": h},
            "frames": e["frames"],
            "fps": e["fps"],
            "steps": e["steps"],
            "sampler_name": e["sampler"],
            "scheduler": e["scheduler"],
            "guidance_scale": e["cfg"],
            "denoise": e["denoise"],
            "model_sampling": {"enabled": True, "type": "sd3",
                               "shift": resolve_preset_shift(e, mode)},
            "memory_optimization": e["memory_optimization"],
            "model_offload": e["model_offload"],
            "offload_policy": "",
            "unload_model_after_generation": e["unload_after_generation"],
            "step_range": list(e["step_range"]),
            "cfg_range": list(e["cfg_range"]),
            "warning": e["warning"],
        }

    # Legacy patch8 presets.
    p = _LEGACY_PROFILES[pid]
    w, h = _legacy_resolution(pid, orientation)
    return {
        "wan_preset": pid,
        "profile": PROFILE_NORMAL,
        "orientation": (orientation or "landscape").lower(),
        "resolution": {"width": w, "height": h},
        "frames": getattr(settings, p["frames_key"]),
        "fps": settings.wan_default_fps,
        "steps": getattr(settings, p["steps_key"]),
        "sampler_name": "euler",
        "scheduler": "simple",
        "guidance_scale": p["cfg"],
        "denoise": 1.0,
        "model_sampling": {"enabled": True, "type": "sd3",
                           "shift": resolve_preset_shift({}, mode)},
        "memory_optimization": True,
        "model_offload": False,
        "offload_policy": p["offload_policy"],
        "unload_model_after_generation": p["unload_after_generation"],
        "step_range": list(p["step_range"]),
        "cfg_range": list(p["cfg_range"]),
        "warning": p["warning"],
    }


# --- Warning system (patchoptimization §7) ------------------------------------
def profile_mismatch_warning(model_profile: str, preset_profile: str) -> dict | None:
    """Warning for a model/preset profile mismatch. Returns {level, message} or
    None. 'blocking' means the UI must ask for explicit confirmation (§7.1)."""
    if model_profile in ACCELERATED_PROFILES and preset_profile == PROFILE_NORMAL:
        return {"level": "blocking", "message":
                "Turbo/Lightning-style model detected. Normal Wan2.2 presets may "
                "cause flicker, colored flashes, overexposure, or unstable frames. "
                "Use a Turbo preset with CFG 1.0 and 4–6 steps."}
    if model_profile == PROFILE_NORMAL and preset_profile == PROFILE_TURBO:
        return {"level": "warning", "message":
                "Turbo preset selected for a normal Wan2.2 model. The render will "
                "be fast but may have lower quality because steps and CFG are very low."}
    return None


def _is_heavy(width: int, height: int, frames: int, steps: int) -> bool:
    return (max(width, height) >= 1280) or (frames >= 81 and steps >= 24)


def heavy_preset_warning(target: dict) -> dict | None:
    """Heavy render warning for a preset target (§7.3)."""
    res = target.get("resolution", {})
    if _is_heavy(res.get("width", 0), res.get("height", 0),
                 target.get("frames", 0), target.get("steps", 0)):
        return {"level": "warning", "message": _HEAVY_WARNING}
    return None


def preset_changes(project, preset_id: str) -> dict:
    """Compute a preset's target values, a human-readable diff vs the project's
    current settings, and any model/preset mismatch or heavy-render warnings
    (patch8 §3 + patchoptimization §7). Never applies anything."""
    pid = normalize_preset(preset_id)
    mode = project.generation_mode.value
    orientation = project.orientation.value
    target = compute_preset(pid, mode, orientation)
    model_profile = detect_profile_for_project(project)
    if target is None:
        return {"preset": MANUAL, "target": None, "changes": [], "warning": None,
                "warnings": [], "requires_confirmation": False,
                "detected_model_profile": model_profile, "preset_profile": "manual"}

    p = project.params
    changes: list[str] = []

    def _chg(label, old, new, fmt=str):
        if str(old) != str(new):
            changes.append(f"{label} {fmt(old)} → {fmt(new)}")

    cur_res = f"{project.resolution.width}x{project.resolution.height}"
    new_res = f"{target['resolution']['width']}x{target['resolution']['height']}"
    _chg("Resolution", cur_res, new_res)
    _chg("Frames", p.frames, target["frames"])
    _chg("FPS", p.fps, target["fps"])
    _chg("Steps", p.advanced.steps, target["steps"])
    _chg("Sampler", p.sampler_name, target["sampler_name"])
    _chg("Scheduler", p.scheduler, target["scheduler"])
    _chg("CFG", f"{p.guidance_scale:.1f}", f"{target['guidance_scale']:.1f}")
    _chg("Denoise", f"{p.denoise:.2f}", f"{target['denoise']:.2f}")

    ms = target["model_sampling"]
    if not p.model_sampling.enabled and ms["enabled"]:
        changes.append("ModelSamplingSD3 enabled")
    if abs(p.model_sampling.shift - ms["shift"]) > 1e-6 or not p.model_sampling.enabled:
        changes.append(f"ModelSamplingSD3 shift set to {ms['shift']:.1f} "
                       f"({'I2V' if mode == 'image2video' else 'T2V'})")
    _chg("Offload policy", getattr(p.advanced, "offload_policy", "") or "(env default)",
         target["offload_policy"] or "(env default)")
    if target["unload_model_after_generation"] and not getattr(
            p.advanced, "unload_model_after_generation", False):
        changes.append("Unload model after generation enabled")

    warnings: list[dict] = []
    mism = profile_mismatch_warning(model_profile, target.get("profile", PROFILE_NORMAL))
    if mism:
        warnings.append(mism)
    heavy = heavy_preset_warning(target)
    if heavy:
        warnings.append(heavy)
    if target.get("warning"):
        warnings.append({"level": "info", "message": target["warning"]})
    requires_confirmation = any(w["level"] == "blocking" for w in warnings)

    return {"preset": pid, "target": target, "changes": changes,
            "warning": target["warning"], "warnings": warnings,
            "requires_confirmation": requires_confirmation,
            "detected_model_profile": model_profile,
            "preset_profile": target.get("profile", PROFILE_NORMAL)}


def recommend_preset(vram_gb: float | None, profile: str | None = None) -> dict:
    """Non-restrictive recommendation (patch8 §12 + patchoptimization §8). When
    an accelerated model is detected, recommend a Turbo preset regardless of VRAM."""
    if profile in ACCELERATED_PROFILES:
        return {"recommended": "wan22_5b_turbo_preview_landscape",
                "alternatives": ["wan22_5b_turbo_quality_landscape"],
                "reason": f"{profile.capitalize()}-style model detected — use a Turbo "
                          "preset (CFG 1.0, 4–6 steps) to avoid flicker/color flashes.",
                "vram_gb": vram_gb, "profile": profile}
    if vram_gb is None:
        rec, reason = ("wan22_5b_fast_preview_landscape",
                       "VRAM unknown — try Fast Preview first, then Safe Quality.")
    elif vram_gb >= 11.5:
        rec, reason = ("wan22_5b_safe_quality_landscape",
                       f"{vram_gb:.1f} GB VRAM — Safe Quality is recommended.")
    elif vram_gb >= 8:
        rec, reason = ("wan22_5b_fast_preview_landscape",
                       f"{vram_gb:.0f} GB VRAM — Fast Preview recommended.")
    else:
        rec, reason = (LOW_VRAM, f"{vram_gb:.0f} GB VRAM — Low VRAM strongly recommended.")
    return {"recommended": rec, "alternatives": ["wan22_5b_fast_preview_landscape"],
            "reason": reason, "vram_gb": vram_gb, "profile": profile or PROFILE_NORMAL}


def generation_profile_metadata(project, bundle: dict | None = None) -> dict:
    """Detected-profile / preset / effective-shift block recorded with every
    generation, diagnostics report and ComfyUI export (patchoptimization §10).

    turbo_parameter_warning is DERIVED from the real project parameters — it
    never rewrites user values, it only reports the risk (§9)."""
    profile = detect_wan_speed_profile(bundle) if bundle is not None \
        else detect_profile_for_project(project)
    pid = normalize_preset(getattr(project.params, "wan_preset", MANUAL))
    preset_profile = preset_profile_of(pid)
    mode = project.generation_mode.value
    ms = getattr(project.params, "model_sampling", None)
    ms_enabled = bool(ms and ms.enabled)

    turbo_warning = bool(
        profile in ACCELERATED_PROFILES
        and (project.params.guidance_scale > 1.5 or project.params.advanced.steps > 8))
    turbo_message = (
        "Turbo/Lightning-style model with high CFG or high steps may cause "
        "flicker/color flashes. Turbo models work best at CFG 1.0–1.2 and 4–6 steps."
        if turbo_warning else None)

    mism = profile_mismatch_warning(profile, preset_profile)
    return {
        "detected_model_profile": profile,
        "selected_preset_id": pid,
        "selected_preset_profile": preset_profile,
        "preset_warning": mism["message"] if mism else None,
        "model_sampling_shift_mode": "i2v" if mode == "image2video" else "t2v",
        "effective_model_sampling_shift": (ms.shift if ms_enabled else None),
        "turbo_parameter_warning": turbo_warning,
        "turbo_parameter_message": turbo_message,
    }


def preset_metadata(project) -> dict:
    """Requested-vs-effective preset block for generation metadata (patch8 §22/§27).
    `requested` is the preset's canonical values; `effective` is what the project
    actually uses now (so user edits after applying a preset are visible)."""
    pid = normalize_preset(getattr(project.params, "wan_preset", MANUAL))
    mode = project.generation_mode.value
    orientation = project.orientation.value
    requested = compute_preset(pid, mode, orientation)
    effective = {
        "resolution": {"width": project.resolution.width,
                       "height": project.resolution.height},
        "frames": project.params.frames,
        "fps": project.params.fps,
        "steps": project.params.advanced.steps,
        "sampler_name": project.params.sampler_name,
        "scheduler": project.params.scheduler,
        "guidance_scale": project.params.guidance_scale,
        "denoise": project.params.denoise,
        "model_sampling": {
            "enabled": project.params.model_sampling.enabled,
            "type": project.params.model_sampling.type,
            "shift": project.params.model_sampling.shift,
        },
    }
    matches = requested is None or all(
        str(effective.get(k)) == str(requested.get(k))
        for k in ("frames", "fps", "steps", "sampler_name", "scheduler"))
    return {"preset": pid, "requested": requested, "effective": effective,
            "matches_preset": bool(matches)}
