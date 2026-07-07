"""Sampler capability registry and fallback resolution (patch6c).

This module makes sampler selection *real*. It separates three concerns that
the old code conflated (patch6c §6):

  - ``COMFYUI_SAMPLERS``        — samplers a Wan2.2 ComfyUI workflow can use.
  - ``DIRECT_BACKEND_SAMPLERS`` — samplers the direct diffusers backend can
                                  *actually* run, each backed by a real
                                  flow-matching scheduler class in
                                  ``wan_backend.build_direct_scheduler`` (§7).
  - the *effective* runtime sampler, resolved per generation below.

Why the direct set is a subset: the direct backend renders Wan2.2, a
flow-matching model. Only schedulers that implement flow matching (flow sigmas /
rectified-flow steps) produce correct results on it. Epsilon/v-prediction
samplers (``ddim``, ``lms``, ``dpm_2`` …) would produce garbage, so they are
honestly reported as unsupported by the direct backend — never silently swapped
for another sampler (patch6c §2/§7/§18).
"""

from __future__ import annotations

from dataclasses import dataclass, field

# patch6c §6 — ComfyUI Wan2.2 KSampler-compatible sampler names.
COMFYUI_SAMPLERS = frozenset({
    "euler",
    "euler_ancestral",
    "heun",
    "dpm_2",
    "dpm_2_ancestral",
    "lms",
    "dpmpp_2m",
    "dpmpp_2m_sde",
    "dpmpp_sde",
    "ddim",
    "uni_pc",
})

# patch6c §6/§7 — samplers the direct diffusers backend can genuinely run. Each
# name here MUST have a real scheduler mapping in
# ``wan_backend.build_direct_scheduler``; do not add a name without one.
#   uni_pc        -> UniPCMultistepScheduler        (flow_prediction)
#   euler         -> FlowMatchEulerDiscreteScheduler
#   heun          -> FlowMatchHeunDiscreteScheduler
#   dpmpp_2m      -> DPMSolverMultistepScheduler     (flow, dpmsolver++)
#   dpmpp_2m_sde  -> DPMSolverMultistepScheduler     (flow, sde-dpmsolver++)
DIRECT_BACKEND_SAMPLERS = frozenset({
    "uni_pc",
    "euler",
    "heun",
    "dpmpp_2m",
    "dpmpp_2m_sde",
})

# The direct sampler used when a fallback is explicitly permitted (ask /
# allow_with_warning). It is the model's native flow-matching solver.
DEFAULT_DIRECT_SAMPLER = "uni_pc"

# Normalize UI / legacy / alias names to the canonical KSampler name (patch6c §7).
SAMPLER_ALIASES = {
    "unipc": "uni_pc", "uni_pc": "uni_pc",
    "euler": "euler",
    "euler_a": "euler_ancestral", "euler_ancestral": "euler_ancestral",
    "heun": "heun",
    "ddim": "ddim",
    "lms": "lms",
    "dpm_2": "dpm_2", "dpm2": "dpm_2",
    "dpm_2_ancestral": "dpm_2_ancestral", "dpm2_ancestral": "dpm_2_ancestral",
    "dpmpp_2m": "dpmpp_2m", "dpm++_2m": "dpmpp_2m", "dpm++": "dpmpp_2m",
    "dpmpp_2m_sde": "dpmpp_2m_sde", "dpm++_2m_sde": "dpmpp_2m_sde",
    "dpmpp_sde": "dpmpp_sde", "dpm++_sde": "dpmpp_sde",
    "dpm_fast": "dpm_fast", "dpm_adaptive": "dpm_adaptive",
}

FALLBACK_POLICIES = ("block", "ask", "route_to_comfyui", "allow_with_warning")
DEFAULT_FALLBACK_POLICY = "block"


def normalize_sampler(name: str) -> str:
    key = (name or "").strip().lower()
    return SAMPLER_ALIASES.get(key, key)


def direct_backend_supports(name: str) -> bool:
    return normalize_sampler(name) in DIRECT_BACKEND_SAMPLERS


def comfyui_supports(name: str) -> bool:
    return normalize_sampler(name) in COMFYUI_SAMPLERS


def normalize_policy(name: str) -> str:
    key = (name or "").strip().lower()
    return key if key in FALLBACK_POLICIES else DEFAULT_FALLBACK_POLICY


def capabilities() -> dict:
    """Capability snapshot for the backend-status API and the UI (patch6c §9)."""
    return {
        "comfyui_samplers": sorted(COMFYUI_SAMPLERS),
        "direct_backend_samplers": sorted(DIRECT_BACKEND_SAMPLERS),
        "default_direct_sampler": DEFAULT_DIRECT_SAMPLER,
    }


@dataclass
class SamplingResolution:
    """Outcome of resolving a requested sampler against a render backend and the
    configured fallback policy (patch6c §3/§4/§10). Nothing here is silent: every
    field is surfaced in the UI, metadata and logs."""

    requested_sampler: str
    selected_backend: str = "direct"
    effective_backend: str = "direct"
    effective_sampler: str | None = None
    fallback_policy: str = "block"
    fallback_applied: bool = False
    routed_to_comfyui: bool = False
    blocked: bool = False
    blocked_reason: str | None = None
    needs_confirmation: bool = False
    confirmation_message: str | None = None
    warnings: list[str] = field(default_factory=list)
    direct_supported: bool = True
    comfyui_supported: bool = True

    @property
    def status(self) -> str:
        if self.blocked:
            return "blocked"
        if self.needs_confirmation:
            return "needs_confirmation"
        if self.routed_to_comfyui:
            return "routed_to_comfyui"
        if self.fallback_applied:
            return "allow_with_warning"
        return "ok"

    def as_metadata(self) -> dict:
        """The `sampler_backend` metadata block (patch6c §12)."""
        return {
            "selected_render_backend": self.selected_backend,
            "effective_render_backend": self.effective_backend,
            "requested_sampler_name": self.requested_sampler,
            "effective_sampler_name": self.effective_sampler,
            "fallback_policy": self.fallback_policy,
            "fallback_applied": self.fallback_applied,
            "routed_to_comfyui": self.routed_to_comfyui,
            "generation_started": not self.blocked,
            "blocked_reason": self.blocked_reason,
            "direct_backend_supported": self.direct_supported,
            "comfyui_backend_supported": self.comfyui_supported,
            "status": self.status,
        }


def resolve_sampling(
    requested_sampler: str,
    *,
    selected_backend: str = "direct",
    fallback_policy: str = "block",
    comfyui_available: bool = False,
    warn_on_fallback: bool = True,
    confirm_fallback: bool = False,
) -> SamplingResolution:
    """Decide which sampler/backend a generation will actually use.

    The absolute rule (patch6c §2): if the selected backend cannot use the
    requested sampler, this never returns a silent substitution — it either
    routes to ComfyUI, blocks, or (only when explicitly permitted) reports a
    visible fallback.
    """
    canonical = normalize_sampler(requested_sampler) or DEFAULT_DIRECT_SAMPLER
    policy = normalize_policy(fallback_policy)
    res = SamplingResolution(
        requested_sampler=canonical,
        selected_backend=selected_backend,
        effective_backend=selected_backend,
        fallback_policy=policy,
        direct_supported=canonical in DIRECT_BACKEND_SAMPLERS,
        comfyui_supported=canonical in COMFYUI_SAMPLERS,
    )

    # The user explicitly selected the ComfyUI backend.
    if selected_backend == "comfyui":
        if canonical in COMFYUI_SAMPLERS:
            res.effective_sampler = canonical
        else:
            res.blocked = True
            res.blocked_reason = (
                f'The ComfyUI backend does not recognise sampler "{canonical}".')
        return res

    # Direct diffusers backend — the common path.
    if canonical in DIRECT_BACKEND_SAMPLERS:
        res.effective_sampler = canonical  # used exactly, no fallback
        return res

    # Unsupported by the direct backend — apply the fallback policy. Never silent.
    if policy == "route_to_comfyui":
        if comfyui_available and canonical in COMFYUI_SAMPLERS:
            res.effective_backend = "comfyui"
            res.routed_to_comfyui = True
            res.effective_sampler = canonical
            if warn_on_fallback:
                res.warnings.append(
                    f'Requested sampler "{canonical}" is not supported by the direct '
                    "backend. Generation will be routed to the ComfyUI backend to "
                    "preserve exact sampler behavior.")
            return res
        res.blocked = True
        res.blocked_reason = (
            f'Direct backend does not support sampler "{canonical}", and the ComfyUI '
            "backend is not available (set COMFYUI_API_ENABLED=true and make sure "
            "ComfyUI is reachable). Choose a supported direct-backend sampler ("
            + ", ".join(sorted(DIRECT_BACKEND_SAMPLERS)) + ") instead.")
        return res

    if policy == "ask":
        if confirm_fallback:
            res.effective_sampler = DEFAULT_DIRECT_SAMPLER
            res.fallback_applied = True
            res.warnings.append(
                f'Sampler fallback confirmed by user: requested "{canonical}" is not '
                f'implemented in the direct backend; "{DEFAULT_DIRECT_SAMPLER}" was '
                "used instead.")
            return res
        res.needs_confirmation = True
        res.effective_sampler = DEFAULT_DIRECT_SAMPLER
        res.confirmation_message = (
            f'The direct backend cannot use sampler "{canonical}". It would use '
            f'"{DEFAULT_DIRECT_SAMPLER}" instead. Continue anyway?')
        return res

    if policy == "allow_with_warning":
        res.effective_sampler = DEFAULT_DIRECT_SAMPLER
        res.fallback_applied = True
        res.warnings.append(
            f'Sampler fallback applied (debug mode): requested "{canonical}" is not '
            f'implemented in the direct backend; "{DEFAULT_DIRECT_SAMPLER}" was used '
            "instead. Enable route_to_comfyui or the ComfyUI backend for exact "
            "sampler behavior.")
        return res

    # Default policy: block. Do not start generation with the wrong sampler.
    res.blocked = True
    res.blocked_reason = (
        f'Direct backend does not currently support sampler "{canonical}". '
        "Use a supported direct-backend sampler ("
        + ", ".join(sorted(DIRECT_BACKEND_SAMPLERS)) + ") or render through the "
        "ComfyUI backend to preserve exact sampler behavior.")
    return res
