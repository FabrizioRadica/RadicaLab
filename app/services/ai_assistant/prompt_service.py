"""SingleClipPromptService + SequencePromptPlannerService (patch §6/§7/§8).

Build the chat messages, call the configured provider (via the resource
manager's guard + release), and parse the response into the typed result models.
Never starts a render (patch §2.3).
"""

from __future__ import annotations

from app.config import logger
from app.models.ai_assistant_models import (
    AIAssistantConfig,
    SequencePlan,
    SingleClipPromptResult,
)
from app.services.ai_assistant import parser
from app.services.ai_assistant.providers import get_provider
from app.services.ai_assistant.resource_manager import (
    STATUS_GENERATING_PROMPT,
    STATUS_GENERATING_SEQUENCE,
    STATUS_IDLE,
    resource_manager,
)

# patch §8 — default internal system prompt (overridable in settings).
DEFAULT_SYSTEM_PROMPT = """\
You are the WanVideoGenerator Prompt Assistant.

Your task is to help the user create high-quality positive and negative prompts \
for video generation, and when requested, to plan multi-clip cinematic sequences \
for the existing SequenceQueue system.

You must preserve visual continuity across clips, including subject identity, \
style, lighting, camera language, color mood, resolution, duration, and motion \
coherence.

When generating a sequence, return a structured list of clips compatible with the \
existing SequenceQueue data model. Do not start rendering. Rendering must always \
remain under explicit user control.

Do not invent unsupported generation parameters. Use only fields supported by the \
current project. For each clip provide: clip name, clip type (Text2Video or \
Image2Video), positive prompt, negative prompt, duration, optional camera notes, \
optional motion notes, optional continuity notes.

Negative prompts must focus on common video generation artifacts such as bad \
anatomy, broken hands, distorted limbs, unstable body motion, face distortion, \
flickering, identity drift, jitter, unwanted camera jumps, low quality, blur, \
noise, text, watermark and logo.

Never automatically start generation or rendering. Return machine-readable JSON \
when requested."""

# patch §7.4 — default negative-prompt targets for common video artifacts.
DEFAULT_NEGATIVE_TARGETS = (
    "bad anatomy, deformed body, distorted legs, broken legs, extra legs, extra arms, "
    "missing fingers, fused fingers, malformed hands, twisted feet, unnatural walking, "
    "sliding feet, floating body, unstable body, body jitter, face distortion, changing "
    "face, melting face, asymmetrical face, crossed eyes, bad eyes, blinking artifacts, "
    "unnatural smile, plastic skin, doll face, uncanny valley, unrealistic skin texture, "
    "low quality, blurry, noisy, pixelated, compression artifacts, flickering, temporal "
    "inconsistency, identity drift, camera jump, warped background, text, watermark, logo, "
    "frame, cartoon, 3d render"
)


def _system_prompt(config: AIAssistantConfig) -> str:
    override = (config.provider.system_prompt_override or "").strip()
    return override or DEFAULT_SYSTEM_PROMPT


def _build_single_clip_user_message(brief: dict) -> str:
    lines = ["Create a positive and negative prompt for a single video clip.", ""]
    fields = [
        ("Scene description", brief.get("scene")),
        ("Subject", brief.get("subject")),
        ("Style", brief.get("style")),
        ("Camera movement", brief.get("camera")),
        ("Mood", brief.get("mood")),
        ("Environment", brief.get("environment")),
    ]
    for label, value in fields:
        if value and str(value).strip():
            lines.append(f"{label}: {str(value).strip()}")
    only = brief.get("only")  # "positive" | "negative" | None
    if only == "positive":
        lines.append("\nOnly regenerate the positive_prompt; keep negative_prompt concise.")
    elif only == "negative":
        lines.append("\nOnly regenerate the negative_prompt targeting common video artifacts.")
    lines.append(
        "\nRespond ONLY with JSON in this exact shape:\n"
        '{"positive_prompt": "...", "negative_prompt": "...", "notes": "..."}\n'
        "The negative_prompt should target common video generation artifacts such as: "
        + DEFAULT_NEGATIVE_TARGETS + ".")
    return "\n".join(lines)


def _build_sequence_user_message(brief: dict) -> str:
    lines = ["Plan a multi-clip cinematic sequence for the SequenceQueue.", ""]
    if brief.get("description"):
        lines.append(f"Film/sequence description: {str(brief['description']).strip()}")
    if brief.get("num_clips"):
        lines.append(f"Number of clips: {brief['num_clips']}")
    if brief.get("clip_duration"):
        lines.append(f"Default duration per clip (seconds): {brief['clip_duration']}")
    if brief.get("total_duration"):
        lines.append(f"Approximate total duration (seconds): {brief['total_duration']}")
    clip_type = brief.get("clip_type") or "infer"
    lines.append(f"Clip type preference: {clip_type} (Text2Video, Image2Video, or infer per clip)")
    if brief.get("continuity"):
        lines.append(f"Visual continuity requirements: {str(brief['continuity']).strip()}")
    if brief.get("subject_identity"):
        lines.append(f"Subject identity consistency: {str(brief['subject_identity']).strip()}")
    lines.append(
        "\nPreserve subject identity, visual style, lighting direction, color mood, "
        "camera language, environment and motion continuity across all clips.")
    lines.append(
        "\nRespond ONLY with JSON in this exact shape:\n"
        '{"sequence_title": "...", "global_style": "...", "global_negative_prompt": "...", '
        '"clips": [{"clip_name": "...", "clip_type": "Text2Video", "positive_prompt": "...", '
        '"negative_prompt": "...", "duration_seconds": 4, "camera_notes": "...", '
        '"motion_notes": "...", "continuity_notes": "...", "image_reference_required": false}]}\n'
        "Negative prompts should target common video artifacts such as: "
        + DEFAULT_NEGATIVE_TARGETS + ".")
    return "\n".join(lines)


async def _run(config: AIAssistantConfig, user_message: str, status: str) -> tuple[str, dict]:
    provider = get_provider(config.provider)
    is_local = config.provider.is_local()
    lock = resource_manager.acquire(config.resources, is_local)
    messages = [
        {"role": "system", "content": _system_prompt(config)},
        {"role": "user", "content": user_message},
    ]
    async with lock:
        # --- Phase 1: generation. A failure here is a real generation error
        # and is allowed to propagate to the caller (patch §10). ---
        resource_manager._busy = True
        resource_manager.set_status(status)
        try:
            text = await provider.generate_chat_completion(messages)
        finally:
            resource_manager._busy = False

        # --- Phase 2: resource cleanup. Strictly separated from Phase 1 so a
        # cleanup failure can NEVER discard a successful result (patch §5/§10).
        # release() already never raises, but keep an extra safety guard. ---
        try:
            release = await resource_manager.release(provider, config.resources, is_local)
        except Exception as exc:  # noqa: BLE001 — must not fail a successful generation
            logger.warning("AI resource release raised unexpectedly (non-fatal): %s", exc)
            release = {"ok": False, "released": False,
                       "warning": f"Prompt generated, but resource cleanup failed: {exc}",
                       "reason": "cleanup_exception"}
    if not is_local:
        resource_manager.set_status(STATUS_IDLE)
    return text, release


async def generate_single_clip(config: AIAssistantConfig, brief: dict) -> tuple[SingleClipPromptResult, dict]:
    text, release = await _run(config, _build_single_clip_user_message(brief), STATUS_GENERATING_PROMPT)
    return parser.parse_single_clip(text), release


async def generate_sequence_plan(config: AIAssistantConfig, brief: dict) -> tuple[SequencePlan, dict]:
    text, release = await _run(config, _build_sequence_user_message(brief), STATUS_GENERATING_SEQUENCE)
    return parser.parse_sequence_plan(text), release
