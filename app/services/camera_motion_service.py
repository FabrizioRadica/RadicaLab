"""Camera Motion Assistant — turns visual motion settings into prompt text."""

from __future__ import annotations

from app.models.camera_motion_models import CameraMotionSettings, MovementType

MOVEMENT_LABELS: dict[str, str] = {
    "static": "Static",
    "push_in": "Push In",
    "pull_back": "Pull Back",
    "pan_left": "Pan Left",
    "pan_right": "Pan Right",
    "tilt_up": "Tilt Up",
    "tilt_down": "Tilt Down",
    "orbit_left": "Orbit Left",
    "orbit_right": "Orbit Right",
    "truck_left": "Truck Left",
    "truck_right": "Truck Right",
    "crane_up": "Crane Up",
    "crane_down": "Crane Down",
    "handheld": "Handheld",
    "follow": "Follow Subject",
    "parallax": "Parallax",
}

_BASE_PHRASES: dict[MovementType, str] = {
    MovementType.STATIC: "static camera, locked-off shot, stable framing",
    MovementType.PUSH_IN: "camera push in towards the subject",
    MovementType.PULL_BACK: "camera pull back revealing the scene",
    MovementType.PAN_LEFT: "camera panning left across the scene",
    MovementType.PAN_RIGHT: "camera panning right across the scene",
    MovementType.TILT_UP: "camera tilting up",
    MovementType.TILT_DOWN: "camera tilting down",
    MovementType.ORBIT_LEFT: "orbit left around the subject",
    MovementType.ORBIT_RIGHT: "orbit right around the subject",
    MovementType.TRUCK_LEFT: "camera trucking left, side tracking shot",
    MovementType.TRUCK_RIGHT: "camera trucking right, side tracking shot",
    MovementType.CRANE_UP: "crane shot rising upward",
    MovementType.CRANE_DOWN: "crane shot descending",
    MovementType.HANDHELD: "subtle handheld camera movement, organic realism",
    MovementType.FOLLOW: "camera following the subject",
    MovementType.PARALLAX: "lateral camera movement with strong foreground parallax",
}

PRESETS: list[dict] = [
    {
        "id": "cinematic_slow_orbit",
        "name": "Cinematic slow orbit",
        "settings": {"movement_type": "orbit_left", "intensity": 0.6, "speed": 0.25, "smoothness": 0.9,
                     "orbit_angle": 360, "path_type": "full_circle", "framing": "stable"},
    },
    {
        "id": "subtle_handheld",
        "name": "Subtle handheld realism",
        "settings": {"movement_type": "handheld", "intensity": 0.3, "speed": 0.4, "smoothness": 0.5,
                     "path_type": "free", "framing": "dynamic"},
    },
    {
        "id": "smooth_push_in",
        "name": "Smooth push in",
        "settings": {"movement_type": "push_in", "intensity": 0.55, "speed": 0.35, "smoothness": 0.9,
                     "path_type": "linear", "framing": "centered"},
    },
    {
        "id": "dramatic_pull_back",
        "name": "Dramatic pull back",
        "settings": {"movement_type": "pull_back", "intensity": 0.8, "speed": 0.5, "smoothness": 0.8,
                     "path_type": "linear", "framing": "stable"},
    },
    {
        "id": "side_tracking",
        "name": "Side tracking shot",
        "settings": {"movement_type": "truck_right", "intensity": 0.6, "speed": 0.55, "smoothness": 0.85,
                     "path_type": "linear", "framing": "rule_of_thirds"},
    },
    {
        "id": "vertical_crane_reveal",
        "name": "Vertical crane reveal",
        "settings": {"movement_type": "crane_up", "intensity": 0.7, "speed": 0.4, "smoothness": 0.85,
                     "path_type": "arc", "framing": "dynamic"},
    },
    {
        "id": "static_stable",
        "name": "Static stable framing",
        "settings": {"movement_type": "static", "intensity": 0.0, "speed": 0.0, "smoothness": 1.0,
                     "path_type": "linear", "framing": "stable"},
    },
]


def _speed_word(speed: float) -> str:
    if speed < 0.25:
        return "very slow"
    if speed < 0.45:
        return "slow"
    if speed < 0.7:
        return "steady"
    return "fast"


def _intensity_word(intensity: float) -> str:
    if intensity < 0.25:
        return "subtle"
    if intensity < 0.55:
        return "gentle"
    if intensity < 0.8:
        return "pronounced"
    return "dramatic"


def build_fragment(cm: CameraMotionSettings) -> str:
    """Compose the camera motion prompt fragment from the assistant settings."""
    mt = cm.movement_type
    if mt == MovementType.STATIC:
        return "static camera, locked-off shot, stable framing, no camera movement"

    parts: list[str] = []
    parts.append(f"{_speed_word(cm.speed)} {_intensity_word(cm.intensity)} cinematic {_BASE_PHRASES[mt]}")

    if mt in (MovementType.ORBIT_LEFT, MovementType.ORBIT_RIGHT):
        if cm.path_type.value == "full_circle" or cm.orbit_angle >= 350:
            parts.append("full circular orbit path")
        else:
            parts.append(f"{cm.orbit_angle} degree orbit arc")
        parts.append("constant distance from the subject")

    if cm.smoothness >= 0.7:
        parts.append("smooth fluid camera movement")
    elif cm.smoothness >= 0.4:
        parts.append("natural camera movement")
    else:
        parts.append("raw energetic camera movement")

    if mt in (MovementType.TRUCK_LEFT, MovementType.TRUCK_RIGHT, MovementType.PARALLAX):
        parts.append("subtle parallax between foreground and background")

    if cm.height > 4:
        parts.append("high camera angle")
    elif cm.height < 0.6:
        parts.append("low camera angle")

    framing = {
        "stable": "stable framing",
        "centered": "subject centered in frame",
        "rule_of_thirds": "rule of thirds composition",
        "dynamic": "dynamic framing",
    }[cm.framing.value]
    parts.append(framing)

    return ", ".join(parts)


def get_options() -> dict:
    return {
        "movement_types": [{"id": key, "label": label} for key, label in MOVEMENT_LABELS.items()],
        "path_types": [
            {"id": "full_circle", "label": "Full circle"},
            {"id": "arc", "label": "Arc"},
            {"id": "linear", "label": "Linear"},
            {"id": "free", "label": "Free path"},
        ],
        "framing_modes": [
            {"id": "stable", "label": "Stable"},
            {"id": "centered", "label": "Centered"},
            {"id": "rule_of_thirds", "label": "Rule of thirds"},
            {"id": "dynamic", "label": "Dynamic"},
        ],
        "presets": PRESETS,
    }
