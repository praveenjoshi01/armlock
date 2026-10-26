from enum import Enum
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field, model_validator


class ArmID(str, Enum):
    LEFT = "left"
    RIGHT = "right"


class SpeedSetting(str, Enum):
    SLOW = "slow"
    NORMAL = "normal"
    FAST = "fast"


class ToolName(str, Enum):
    MOVE_ARM = "move_arm"
    GRIP = "grip"
    RELEASE = "release"
    ROTATE_WRIST = "rotate_wrist"
    LOOK = "look"
    DONE = "done"


class MoveArmArgs(BaseModel):
    arm: ArmID = ArmID.LEFT
    dx_cm: float = Field(default=0.0, description="Displacement along X axis in cm")
    dy_cm: float = Field(default=0.0, description="Displacement along Y axis in cm")
    dz_cm: float = Field(default=0.0, description="Displacement along Z axis in cm")
    speed: SpeedSetting = Field(SpeedSetting.NORMAL, description="Speed of movement")


class GripArgs(BaseModel):
    arm: ArmID = ArmID.LEFT


class ReleaseArgs(BaseModel):
    arm: ArmID = ArmID.LEFT


class RotateWristArgs(BaseModel):
    arm: ArmID = ArmID.LEFT
    degrees: float = Field(default=0.0, description="Angle of rotation in degrees")


class LookArgs(BaseModel):
    pass


class DoneArgs(BaseModel):
    summary: str = Field(default="Episode terminated", description="Summary of actions completed")

    @model_validator(mode="before")
    @classmethod
    def normalize_done_args(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "summary" not in data or not data["summary"]:
                for alt_key in ("reason", "message", "text", "description", "note"):
                    if alt_key in data and data[alt_key]:
                        return {"summary": str(data[alt_key])}
                return {"summary": "Episode completed"}
        return data


class ToolCall(BaseModel):
    tool: ToolName
    args: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    success: bool
    message: str
    state_updates: dict[str, Any] = Field(default_factory=dict)


ROBOT_TOOLS_SCHEMA = [
    {
        "name": "move_arm",
        "description": "Move the specified arm by relative displacements dx_cm, dy_cm, dz_cm in centimeters.",
        "parameters": {
            "type": "object",
            "properties": {
                "arm": {"type": "string", "enum": ["left", "right"]},
                "dx_cm": {"type": "number", "description": "Delta X in cm"},
                "dy_cm": {"type": "number", "description": "Delta Y in cm"},
                "dz_cm": {"type": "number", "description": "Delta Z in cm"},
                "speed": {"type": "string", "enum": ["slow", "normal", "fast"]}
            },
            "required": ["arm", "dx_cm", "dy_cm", "dz_cm"]
        }
    },
    {
        "name": "grip",
        "description": "Close the gripper on the specified arm to grasp an object at the current position.",
        "parameters": {
            "type": "object",
            "properties": {
                "arm": {"type": "string", "enum": ["left", "right"]}
            },
            "required": ["arm"]
        }
    },
    {
        "name": "release",
        "description": "Open the gripper on the specified arm to release any grasped object.",
        "parameters": {
            "type": "object",
            "properties": {
                "arm": {"type": "string", "enum": ["left", "right"]}
            },
            "required": ["arm"]
        }
    },
    {
        "name": "rotate_wrist",
        "description": "Rotate the wrist joint of the specified arm by a given angle in degrees.",
        "parameters": {
            "type": "object",
            "properties": {
                "arm": {"type": "string", "enum": ["left", "right"]},
                "degrees": {"type": "number", "description": "Rotation angle in degrees"}
            },
            "required": ["arm", "degrees"]
        }
    },
    {
        "name": "look",
        "description": "Query the overhead vision sensor for the current 3D positions and states of objects on the table.",
        "parameters": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "done",
        "description": "Signal that the assigned instruction has been completed.",
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Summary of actions taken"}
            },
            "required": ["summary"]
        }
    }
]
