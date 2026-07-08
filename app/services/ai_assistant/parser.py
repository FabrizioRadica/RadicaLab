"""PromptResponseParser (patch §7.1).

LLMs frequently wrap JSON in markdown fences or add prose. This parser recovers
the intended structure robustly and degrades gracefully to plain text so the UI
can always show *something* (and offer the raw response) instead of crashing.
"""

from __future__ import annotations

import json
import re

from app.models.ai_assistant_models import (
    ClipPlanType,
    SequencePlan,
    SequencePlanClip,
    SingleClipPromptResult,
)

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def _extract_json_blob(text: str) -> dict | None:
    """Best-effort recovery of a single JSON object from an LLM response."""
    if not text:
        return None
    candidates: list[str] = []
    for match in _FENCE_RE.finditer(text):
        candidates.append(match.group(1).strip())
    candidates.append(text.strip())
    # Also try the substring between the first '{' and the last '}'.
    first, last = text.find("{"), text.rfind("}")
    if first != -1 and last != -1 and last > first:
        candidates.append(text[first:last + 1])
    for cand in candidates:
        try:
            data = json.loads(cand)
            if isinstance(data, dict):
                return data
        except (ValueError, TypeError):
            continue
    return None


def parse_single_clip(text: str) -> SingleClipPromptResult:
    data = _extract_json_blob(text)
    if data:
        return SingleClipPromptResult(
            positive_prompt=str(data.get("positive_prompt") or "").strip(),
            negative_prompt=str(data.get("negative_prompt") or "").strip(),
            notes=str(data.get("notes") or "").strip(),
            raw=text,
            parsed_json=True,
        )
    # Plain-text fallback: use the whole response as the positive prompt so the
    # user still gets a usable result (patch §7.1 robustness).
    return SingleClipPromptResult(
        positive_prompt=text.strip(),
        negative_prompt="",
        notes="The AI did not return JSON; the raw text was used as the positive prompt.",
        raw=text,
        parsed_json=False,
    )


def _coerce_clip_type(value) -> ClipPlanType:
    v = str(value or "").strip().lower().replace("-", "").replace("_", "").replace(" ", "")
    if v in ("image2video", "i2v", "imagetovideo", "imagereference"):
        return ClipPlanType.IMAGE2VIDEO
    return ClipPlanType.TEXT2VIDEO


def _coerce_duration(value, default: float = 4.0) -> float:
    try:
        d = float(value)
    except (TypeError, ValueError):
        return default
    if d <= 0:
        return default
    return max(0.1, min(d, 120.0))


def parse_sequence_plan(text: str) -> SequencePlan:
    data = _extract_json_blob(text)
    if not data:
        # No usable structure — return an empty plan carrying the raw text so the
        # UI can show the parse error and offer the raw response (patch §13).
        return SequencePlan(raw=text, parsed_json=False)

    raw_clips = data.get("clips")
    clips: list[SequencePlanClip] = []
    if isinstance(raw_clips, list):
        for i, rc in enumerate(raw_clips):
            if not isinstance(rc, dict):
                continue
            name = str(rc.get("clip_name") or rc.get("name") or f"Clip {i + 1:02d}").strip()
            clips.append(SequencePlanClip(
                clip_name=name or f"Clip {i + 1:02d}",
                clip_type=_coerce_clip_type(rc.get("clip_type") or rc.get("type")),
                positive_prompt=str(rc.get("positive_prompt") or rc.get("prompt") or "").strip(),
                negative_prompt=str(rc.get("negative_prompt") or "").strip(),
                duration_seconds=_coerce_duration(rc.get("duration_seconds") or rc.get("duration")),
                camera_notes=str(rc.get("camera_notes") or "").strip(),
                motion_notes=str(rc.get("motion_notes") or "").strip(),
                continuity_notes=str(rc.get("continuity_notes") or "").strip(),
                image_reference_required=bool(rc.get("image_reference_required", False)),
            ))
    return SequencePlan(
        sequence_title=str(data.get("sequence_title") or "").strip(),
        global_style=str(data.get("global_style") or "").strip(),
        global_negative_prompt=str(data.get("global_negative_prompt") or "").strip(),
        clips=clips,
        raw=text,
        parsed_json=True,
    )
