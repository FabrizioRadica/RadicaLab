"""Camera Motion Assistant data models."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class MovementType(str, Enum):
    STATIC = "static"
    PUSH_IN = "push_in"
    PULL_BACK = "pull_back"
    PAN_LEFT = "pan_left"
    PAN_RIGHT = "pan_right"
    TILT_UP = "tilt_up"
    TILT_DOWN = "tilt_down"
    ORBIT_LEFT = "orbit_left"
    ORBIT_RIGHT = "orbit_right"
    TRUCK_LEFT = "truck_left"
    TRUCK_RIGHT = "truck_right"
    CRANE_UP = "crane_up"
    CRANE_DOWN = "crane_down"
    HANDHELD = "handheld"
    FOLLOW = "follow"
    PARALLAX = "parallax"


class PathType(str, Enum):
    FULL_CIRCLE = "full_circle"
    ARC = "arc"
    LINEAR = "linear"
    FREE = "free"


class FramingMode(str, Enum):
    STABLE = "stable"
    CENTERED = "centered"
    RULE_OF_THIRDS = "rule_of_thirds"
    DYNAMIC = "dynamic"


class CameraMotionSettings(BaseModel):
    enabled: bool = False
    movement_type: MovementType = MovementType.STATIC
    intensity: float = Field(default=0.65, ge=0.0, le=1.0)
    speed: float = Field(default=0.55, ge=0.0, le=1.0)
    smoothness: float = Field(default=0.8, ge=0.0, le=1.0)
    orbit_angle: int = Field(default=360, ge=15, le=360)
    distance: float = Field(default=3.5, ge=0.5, le=20.0)
    height: float = Field(default=1.5, ge=-5.0, le=15.0)
    path_type: PathType = PathType.ARC
    framing: FramingMode = FramingMode.STABLE
    preset: str | None = None
    fragment: str = ""
    applied_to_prompt: bool = False
