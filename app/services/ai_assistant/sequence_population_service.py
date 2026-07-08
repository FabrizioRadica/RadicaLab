"""SequenceQueuePopulationService (patch §9).

Maps a validated AI SequencePlan onto the EXISTING VideoSequenceQueue model and
persists through the existing `sequence_service` — the SequenceQueue stays the
single source of truth (patch §2.2). Never starts a render (patch §2.3/§9.1).
"""

from __future__ import annotations

import uuid

from app.config import logger
from app.models.ai_assistant_models import ClipPlanType, SequencePlan
from app.models.sequence_models import (
    ClipGenerationOverrides,
    ClipType,
    SequenceClip,
)
from app.services import sequence_service
from app.services.sequence_service import SequenceError

MIN_DURATION_S = 0.2
MAX_DURATION_S = 60.0
MAX_FRAMES = 1000
MIN_FRAMES = 5


class PopulationError(Exception):
    """Validation/population failure with a user-readable message."""


def _snap_frames(frames: int) -> int:
    """Snap to Wan's 4k+1 frame constraint (matches the UI snapFrames helper)."""
    frames = max(MIN_FRAMES, min(int(frames), MAX_FRAMES))
    k = round((frames - 1) / 4)
    return max(MIN_FRAMES, min(4 * k + 1, MAX_FRAMES))


def validate_plan(plan: SequencePlan) -> list[str]:
    """Validate a plan before touching the queue (patch §9.3). Returns non-fatal
    notes; raises PopulationError on anything that must block insertion."""
    if not plan.clips:
        raise PopulationError("The generated sequence has no clips.")
    notes: list[str] = []
    for i, clip in enumerate(plan.clips):
        label = f"Clip {i + 1}"
        if not (clip.clip_name or "").strip():
            raise PopulationError(f"{label} has no name.")
        if not (clip.positive_prompt or "").strip():
            raise PopulationError(f"'{clip.clip_name}' has an empty positive prompt.")
        if not (MIN_DURATION_S <= clip.duration_seconds <= MAX_DURATION_S):
            raise PopulationError(
                f"'{clip.clip_name}' duration {clip.duration_seconds}s is outside the "
                f"supported range {MIN_DURATION_S}-{MAX_DURATION_S}s.")
        if clip.clip_type == ClipPlanType.IMAGE2VIDEO:
            notes.append(
                f"'{clip.clip_name}' is an Image2Video clip — add a source image to it "
                "in the queue before rendering.")
    return notes


def _to_clip(plan: SequencePlan, planned, index: int, global_fps: int,
             global_frames: int) -> SequenceClip:
    ctype = (ClipType.IMAGE_REFERENCE if planned.clip_type == ClipPlanType.IMAGE2VIDEO
             else ClipType.PROMPT_ONLY)
    # Compose negative prompt: clip-specific first, else the plan-global one.
    negative = (planned.negative_prompt or "").strip() or (plan.global_negative_prompt or "").strip()

    # Honor the planned per-clip duration via a frames override only when it
    # differs from the sequence default; otherwise inherit the global settings.
    desired_frames = _snap_frames(round(planned.duration_seconds * max(global_fps, 1)))
    use_global = (desired_frames == _snap_frames(global_frames))
    overrides = ClipGenerationOverrides()
    if not use_global:
        overrides.frames = desired_frames

    # Fold optional AI notes into the clip so nothing is silently dropped (§9.3).
    note_bits = [b for b in (
        f"Camera: {planned.camera_notes}" if planned.camera_notes else "",
        f"Motion: {planned.motion_notes}" if planned.motion_notes else "",
        f"Continuity: {planned.continuity_notes}" if planned.continuity_notes else "",
    ) if b]
    prompt = planned.positive_prompt.strip()

    clip = SequenceClip(
        clip_id=uuid.uuid4().hex[:10],
        index=index,
        name=planned.clip_name.strip()[:120] or f"Clip {index + 1:02d}",
        type=ctype,
        prompt=prompt,
        negative_prompt=negative,
        use_global_generation_settings=use_global,
        generation_overrides=overrides,
    )
    if note_bits:
        clip.diagnostics = {"ai_notes": " | ".join(note_bits)}
    return clip


def populate(sequence_id: str, plan: SequencePlan, mode: str = "append") -> dict:
    """Add the plan's clips to the existing SequenceQueue.

    mode: "append" (default) keeps existing clips; "replace" clears them first.
    Refuses to modify a sequence that is currently rendering (patch §14.2)."""
    from app.services.sequence_queue_service import queue_manager

    if mode not in ("append", "replace"):
        raise PopulationError("Queue mode must be 'append' or 'replace'.")
    if queue_manager.is_running(sequence_id):
        raise PopulationError("The sequence is currently rendering. Wait for it to finish.")

    notes = validate_plan(plan)
    try:
        seq = sequence_service.load_sequence(sequence_id)
    except SequenceError as exc:
        raise PopulationError(str(exc)) from exc

    if mode == "replace":
        seq.clips = []

    gfps = seq.global_generation_settings.fps
    gframes = seq.global_generation_settings.frames
    start = len(seq.clips)
    added: list[str] = []
    for i, planned in enumerate(plan.clips):
        clip = _to_clip(plan, planned, start + i, gfps, gframes)
        seq.clips.append(clip)
        added.append(clip.clip_id)

    # Seed the sequence-level default negative prompt if the user hasn't set one.
    if plan.global_negative_prompt and not seq.global_generation_settings.negative_prompt.strip():
        seq.global_generation_settings.negative_prompt = plan.global_negative_prompt.strip()

    seq.reindex()
    sequence_service.save_sequence(seq)
    logger.info("AI populated sequence '%s' (%s): +%d clip(s) [%s]",
                seq.name, seq.sequence_id, len(added), mode)
    return {
        "sequence_id": seq.sequence_id,
        "mode": mode,
        "added": len(added),
        "clip_ids": added,
        "total_clips": len(seq.clips),
        "notes": notes,
    }
