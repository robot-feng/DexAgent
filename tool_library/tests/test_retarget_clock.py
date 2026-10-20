"""Measured hand clocks constrain the final trajectory, including its FK residual."""
import mujoco
import numpy as np
import pytest

from tool_library.skills.retarget_hand_motion import retarget_hand_motion


@pytest.fixture
def hand():
    return mujoco.MjModel.from_xml_string("""
    <mujoco><compiler angle="radian"/><worldbody>
      <body name="hand" pos=".4 .2 .1" quat=".9238795325 0 0 .3826834324">
        <joint name="mount" type="slide" axis="1 0 0"/>
        <geom type="sphere" size=".02"/>
        <body name="finger">
          <joint name="curl" type="hinge" axis="0 0 1" range="-1.5 1.5"/>
          <geom type="capsule" fromto="0 0 0 .1 0 0" size=".01"/>
          <body name="tip" pos=".1 0 0"><geom type="sphere" size=".01"/></body>
        </body>
      </body>
    </worldbody></mujoco>
    """)


def solve(hand, angles, **kwargs):
    angles = np.asarray(angles)
    targets = np.column_stack([.2 * np.cos(angles), .2 * np.sin(angles), np.zeros(len(angles))])
    base = hand.qpos0.copy()
    base[0] = .3
    result = retarget_hand_motion(
        hand, "hand", {"index": "tip"}, {"index": targets},
        tip_points={"index": [.1, 0, 0]}, qpos_base=base, **kwargs,
    )
    return result, targets, base


def fk_errors(hand, result, targets, base):
    data = mujoco.MjData(hand)
    data.qpos[:] = base
    mujoco.mj_kinematics(hand, data)
    root = hand.body("hand").id
    world_targets = targets @ data.xmat[root].reshape(3, 3).T + data.xpos[root]
    errors = []
    tip = hand.body("tip").id
    for q in result["qpos"]:
        data.qpos[:] = base
        data.qpos[result["qadr"]] = q
        mujoco.mj_kinematics(hand, data)
        actual = data.xpos[tip] + data.xmat[tip].reshape(3, 3) @ [.1, 0, 0]
        errors.append(np.linalg.norm(actual - world_targets[len(errors)]))
    return np.asarray(errors)


def test_short_and_long_intervals_limit_returned_joint_speeds(hand):
    times = np.array([6.0, 6.001, 6.501, 6.503])
    result, targets, base = solve(
        hand, [0, 1, 1, -1], timestamps_s=times, max_rate_rad_s=2,
    )
    speeds = np.abs(np.diff(result["qpos"], axis=0)) / np.diff(times)[:, None]
    assert np.max(speeds) <= 2 + 1e-10
    assert result["qpos"][2, 0] > .9  # The long interval permits the requested motion.
    assert not result["ok"]  # Short intervals cannot reach both abrupt target changes.
    errors = fk_errors(hand, result, targets, base)
    assert result["unreachable"]["index"] == np.flatnonzero(errors > .004).tolist()
    assert result["residual_mm"]["index"] == {
        "max_mm": round(float(errors.max()) * 1000, 2),
        "mean_mm": round(float(errors.mean()) * 1000, 2),
    }
    np.testing.assert_array_equal(result["timestamps_s"], times)
    assert result["fps"] is None
    assert result["time_source"] == "timestamps_s"
    assert result["smoothing"] == "disabled_nonuniform_timestamps"


def test_measured_times_ignore_overlay_fps_and_do_not_smooth_irregular_samples(hand):
    angles = np.array([0, .2, -.1, .3, 0, -.2, .1, .25, 0])
    times = np.cumsum([0, .1, .12, .08, .11, .2, .1, .07, .13])
    slow, _, _ = solve(hand, angles, timestamps_s=times, fps=10, max_rate_rad_s=100)
    fast, _, _ = solve(hand, angles, timestamps_s=times, fps=60, max_rate_rad_s=100)
    np.testing.assert_array_equal(slow["qpos"], fast["qpos"])
    np.testing.assert_allclose(slow["qpos"][:, 0], angles, atol=1e-4)
    assert slow["ok"]
    assert slow["smoothing"] == "disabled_nonuniform_timestamps"


def test_uniform_clock_matches_legacy_fps_result(hand):
    angles = np.linspace(0, 1, 20)
    legacy, targets, base = solve(hand, angles, fps=30)
    measured, _, _ = solve(hand, angles, timestamps_s=8 + np.arange(20)/30)
    np.testing.assert_allclose(legacy["qpos"], measured["qpos"], atol=1e-12)
    assert legacy["time_source"] == "fps" and legacy["fps"] == 30
    assert measured["smoothing"] == legacy["smoothing"] == "savgol"
    assert legacy["ok"]
    assert np.max(fk_errors(hand, legacy, targets, base)) < .004


def test_smoothed_trajectory_reports_its_final_target_error(hand):
    result, targets, base = solve(hand, [0, .8] * 10, fps=30, max_rate_rad_s=1000)
    errors = fk_errors(hand, result, targets, base)
    assert result["smoothing"] == "savgol"
    assert not result["ok"]
    assert result["unreachable"]["index"] == np.flatnonzero(errors > .004).tolist()
    assert result["residual_mm"]["index"]["max_mm"] == round(float(errors.max()) * 1000, 2)


def test_single_sample_keeps_original_time_without_inventing_duration(hand):
    result, _, _ = solve(hand, [.6], timestamps_s=[13.766], max_rate_rad_s=0)
    assert result["ok"]
    np.testing.assert_array_equal(result["timestamps_s"], [13.766])
    assert result["smoothing"] == "not_applied_short_track"


@pytest.mark.parametrize("times", [
    [], [0], [0, 1, 2], [[0, 1]], [0, 0], [1, 0], [0, np.nan],
    [0, np.inf], ["0", "1"], [False, True], [0, 1j], [-1e308, 1e308],
])
def test_reject_invalid_measured_clocks(hand, times):
    with pytest.raises(ValueError, match="timestamps_s"):
        solve(hand, [0, .2], timestamps_s=times)


@pytest.mark.parametrize("fps", [0, -1, np.nan, np.inf])
def test_reject_invalid_fallback_frame_rate(hand, fps):
    with pytest.raises(ValueError, match="fps"):
        solve(hand, [0, .2], fps=fps)
