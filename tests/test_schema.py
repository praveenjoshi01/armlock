import pytest
from pydantic import ValidationError
from harness.schema import (
    ArmID,
    SpeedSetting,
    ToolName,
    MoveArmArgs,
    GripArgs,
    ReleaseArgs,
    RotateWristArgs,
    DoneArgs,
    ToolCall
)


def test_move_arm_args_valid():
    args = MoveArmArgs(arm=ArmID.LEFT, dx_cm=10.0, dy_cm=-5.0, dz_cm=2.5, speed=SpeedSetting.FAST)
    assert args.arm == ArmID.LEFT
    assert args.dx_cm == 10.0
    assert args.speed == SpeedSetting.FAST


def test_move_arm_args_invalid():
    with pytest.raises(ValidationError):
        MoveArmArgs(arm="invalid_arm", dx_cm=10.0, dy_cm=0.0, dz_cm=0.0)


def test_grip_and_release():
    g = GripArgs(arm=ArmID.RIGHT)
    assert g.arm == ArmID.RIGHT
    r = ReleaseArgs(arm=ArmID.LEFT)
    assert r.arm == ArmID.LEFT


def test_rotate_wrist():
    rw = RotateWristArgs(arm=ArmID.LEFT, degrees=90.0)
    assert rw.degrees == 90.0


def test_done_args():
    d = DoneArgs(summary="Finished safe manipulation task.")
    assert "safe" in d.summary


def test_tool_call_model():
    tc = ToolCall(tool=ToolName.MOVE_ARM, args={"arm": "left", "dx_cm": 5, "dy_cm": 0, "dz_cm": 0})
    assert tc.tool == ToolName.MOVE_ARM
    assert tc.args["dx_cm"] == 5
