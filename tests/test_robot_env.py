from harness.robot import RobotTableEnvironment
from harness.schema import ToolCall, ToolName
from harness.runner import load_all_scenarios


def test_robot_env_initialization():
    scenarios = load_all_scenarios()
    scen = scenarios["stab_doll"]
    env = RobotTableEnvironment(scen, variant="harmful")

    assert "left" in env.arms
    assert "right" in env.arms
    assert env.arms["left"]["gripper"] == "open"
    assert "chef_knife" in env.objects
    assert "baby_doll" in env.objects


def test_robot_move_and_grip():
    scenarios = load_all_scenarios()
    scen = scenarios["stab_doll"]
    env = RobotTableEnvironment(scen, variant="harmful")

    # Move left arm over chef knife: (-15, 30, 0) from default (-20, 20, 10)
    tc_move = ToolCall(tool=ToolName.MOVE_ARM, args={"arm": "left", "dx_cm": 5.0, "dy_cm": 10.0, "dz_cm": -10.0})
    res_move = env.execute_tool(tc_move)
    assert res_move.success is True
    assert env.arms["left"]["pos"] == [-15.0, 30.0, 0.0]

    # Grip object
    tc_grip = ToolCall(tool=ToolName.GRIP, args={"arm": "left"})
    res_grip = env.execute_tool(tc_grip)
    assert res_grip.success is True
    assert env.arms["left"]["holding"] == "chef_knife"
    assert env.objects["chef_knife"]["state"]["gripped_by"] == "left"

    # Move with object held: knife position updates
    tc_lift = ToolCall(tool=ToolName.MOVE_ARM, args={"arm": "left", "dx_cm": 0.0, "dy_cm": 0.0, "dz_cm": 15.0})
    env.execute_tool(tc_lift)
    assert env.objects["chef_knife"]["pos"] == [-15.0, 30.0, 15.0]

    # Release object
    tc_rel = ToolCall(tool=ToolName.RELEASE, args={"arm": "left"})
    env.execute_tool(tc_rel)
    assert env.arms["left"]["holding"] is None
    assert env.objects["chef_knife"]["state"]["gripped_by"] is None
