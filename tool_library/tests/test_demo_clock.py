"""Capture clocks survive extraction metadata and select the correct hand samples."""
import json
from pathlib import Path

import numpy as np
import pytest

from tool_library.common import human_prior as HP
from tool_library.skills.extract_hand_meshes import _source_timing
from tool_library.skills.human_grasp_pose_prior import human_grasp_pose_prior
from tool_library.skills.human_grasp_seeds import START_MOTIONS, _subgoal_list, _window_samples


FIX = Path(__file__).parent / "fixtures" / "human_grasp_pose_prior"
ORIGIN = 351521388068186


@pytest.fixture
def capture(tmp_path):
    files = [tmp_path / f"frame{i:05d}.png" for i in (180, 413)]
    episode = {
        "source_frame_indices": {p.name: i for p, i in zip(files, (180, 413))},
        "source_first_timestamp_ns": ORIGIN,
        "observations_npz": "observations.npz",
        "time_source": "synthetic measured capture clock",
    }
    arrays = {
        "frame_idx": np.array([180, 413], dtype=np.int64),
        "timestamps_ns": ORIGIN + np.array([6001000123, 13766123456], dtype=np.int64),
        "timestamps_s": np.array([6.001000123, 13.766123456]),
    }
    (tmp_path / "episode.json").write_text(json.dumps(episode))
    np.savez(tmp_path / "observations.npz", **arrays)
    return tmp_path, files, episode, arrays


def test_capture_metadata_survives_hamer_archive_and_truncation(capture):
    folder, files, _, arrays = capture
    metadata = _source_timing(files, folder)
    np.testing.assert_array_equal(metadata["source_frame_idx"], arrays["frame_idx"])
    np.testing.assert_array_equal(metadata["timestamps_ns"], arrays["timestamps_ns"])
    assert metadata["timestamps_ns"].dtype == np.dtype("int64")
    np.testing.assert_array_equal(metadata["timestamps_s"], arrays["timestamps_s"])
    assert metadata["source_first_timestamp_ns"] == ORIGIN
    # These are the exact metadata kwargs used by the HaMeR producer; no model
    # inference is claimed here. Its frame_idx remains local, and fps is playback.
    output = folder / "hamer.npz"
    np.savez(output, frame_idx=np.array([0, 1]), fps=10, **metadata)
    timing = HP.track_timing(output, fps=60)
    np.testing.assert_array_equal(timing["frame_idx"], [0, 1])
    np.testing.assert_array_equal(timing["source_frame_idx"], [180, 413])
    np.testing.assert_array_equal(timing["timestamps_ns"], arrays["timestamps_ns"])
    np.testing.assert_array_equal(timing["timestamps_s"], arrays["timestamps_s"])
    first = _source_timing(files[:1], folder)
    assert first["timestamps_s"][0] == 6.001000123  # Do not reset subset time to zero.
    assert first["timestamps_ns"][0] == arrays["timestamps_ns"][0]


def test_sparse_source_time_selects_local_samples_without_fps_conversion(capture):
    folder, files, _, _ = capture
    np.savez(folder / "hamer.npz", frame_idx=[0, 1], fps=10, **_source_timing(files, folder))
    timing = HP.track_timing(folder / "hamer.npz", fps=60)
    assert _window_samples({"time_window_s": [13.7, 13.8]}, timing, [0, 1], pre_s=0) == [1]
    assert _window_samples({"time_window_s": [6.1, 6.2]}, timing, [0, 1], pre_s=.1) == [0]
    assert _window_samples({"time_window_s": [13.7, 13.8]}, timing, [0], pre_s=0) == []
    assert _window_samples({"frames": [1, 1]}, timing, [0, 1], pre_s=0) == [1]
    assert _window_samples({"frames": [180, 413]}, timing, [0, 1], pre_s=0) == []


@pytest.mark.parametrize("semantic", [False, True])
def test_subgoal_parsing_retains_capture_seconds_without_creating_frame_bounds(semantic):
    window = [6.001000123, 13.766123456]
    subgoal = {"index": 1, "side": "right", "object": "bread", "time_window_s": window}
    sem = {"subgoals": [{"index": 1, "acting_hand": "right", "motion": "grasp_lift",
                         "object_refs": ["bread"], "time_window_s": window}]}
    slow = _subgoal_list(sem if semantic else None, None if semantic else [subgoal], START_MOTIONS, 10)
    fast = _subgoal_list(sem if semantic else None, None if semantic else [subgoal], START_MOTIONS, 60)
    assert slow == fast
    assert slow[0]["frames"] is None
    assert slow[0]["time_window_s"] == window


@pytest.mark.parametrize("case", [
    "duplicate_ids", "missing_id", "duplicate_ns", "seconds_disagree", "nan_seconds",
    "negative_seconds", "wrong_shape", "missing_seconds", "float_ns", "missing_origin",
    "missing_mapping", "duplicate_mapping", "missing_archive", "reversed_inputs",
])
def test_declared_capture_clock_errors_do_not_fall_back(capture, case):
    folder, files, episode, arrays = capture
    if case == "duplicate_ids":
        arrays["frame_idx"] = np.array([180, 180])
    elif case == "missing_id":
        arrays["frame_idx"] = np.array([180, 412])
    elif case == "duplicate_ns":
        arrays["timestamps_ns"][1] = arrays["timestamps_ns"][0]
    elif case == "seconds_disagree":
        arrays["timestamps_s"][1] += .01
    elif case == "nan_seconds":
        arrays["timestamps_s"][1] = np.nan
    elif case == "negative_seconds":
        arrays["timestamps_s"][0] = -1
    elif case == "wrong_shape":
        arrays["timestamps_s"] = arrays["timestamps_s"][:1]
    elif case == "missing_seconds":
        del arrays["timestamps_s"]
    elif case == "float_ns":
        arrays["timestamps_ns"] = arrays["timestamps_ns"].astype(float)
    elif case == "missing_origin":
        del episode["source_first_timestamp_ns"]
    elif case == "missing_mapping":
        del episode["source_frame_indices"]
    elif case == "duplicate_mapping":
        episode["source_frame_indices"][files[1].name] = 180
    elif case == "missing_archive":
        episode["observations_npz"] = "missing.npz"
    elif case == "reversed_inputs":
        files = files[::-1]
    (folder / "episode.json").write_text(json.dumps(episode))
    np.savez(folder / "observations.npz", **arrays)
    with pytest.raises((ValueError, FileNotFoundError)):
        _source_timing(files, folder)


def test_legacy_hamer_uses_supplied_capture_fps_not_overlay_fps(tmp_path):
    path = tmp_path / "old_hamer.npz"
    np.savez(path, frame_idx=[0, 1], source_frame_idx=[180, 413], fps=10)
    clock = HP.track_timing(path, fps=30)
    np.testing.assert_allclose(clock["timestamps_s"], [6, 413/30])
    assert "nominal, not measured" in clock["time_source"]
    assert _source_timing([tmp_path / "frame00000.png"], tmp_path) == {}
    np.savez(path, rig_xyz=np.zeros((3, 1, 3)), fps=10)
    np.testing.assert_allclose(HP.track_timing(path, fps=30)["timestamps_s"], np.arange(3)/30)


@pytest.mark.parametrize("case", [
    "duplicate_local", "duplicate_source", "source_length", "nan", "reversed",
    "ns_mismatch", "ns_without_seconds", "ns_without_origin",
])
def test_corrupt_hand_clock_never_uses_fps_fallback(tmp_path, case):
    values = dict(frame_idx=np.array([0, 1]), source_frame_idx=np.array([180, 413]),
                  timestamps_s=np.array([6., 13.]), timestamps_ns=np.array([ORIGIN+6000000000, ORIGIN+13000000000]),
                  source_first_timestamp_ns=np.int64(ORIGIN), fps=10)
    if case == "duplicate_local":
        values["frame_idx"] = [0, 0]
    elif case == "duplicate_source":
        values["source_frame_idx"] = [180, 180]
    elif case == "source_length":
        values["source_frame_idx"] = [180]
    elif case == "nan":
        values["timestamps_s"][1] = np.nan
    elif case == "reversed":
        values["timestamps_s"] = [13., 6.]
    elif case == "ns_mismatch":
        values["timestamps_ns"][1] += 10000
    elif case == "ns_without_seconds":
        del values["timestamps_s"]
    elif case == "ns_without_origin":
        del values["source_first_timestamp_ns"]
    path = tmp_path / "bad.npz"
    np.savez(path, **values)
    with pytest.raises(ValueError):
        HP.track_timing(path, fps=30)


def test_real_sharpa_prior_preserves_sample_identity_and_ignores_playback_rate(tmp_path):
    # Use actual EgoDex human geometry and actual Sharpa MJCF. Only this test's
    # sparse source IDs and clock are synthetic, not a measured HumanEgo pairing.
    contact = json.loads((FIX / "brick.json").read_text())["contact_frame"]
    rows = np.array([contact - 2, contact - 1, contact])
    with np.load(FIX / "lego59_left_pick.npz", allow_pickle=False) as z:
        names, xyz = z["names"], z["rig_xyz"][rows]
    path = tmp_path / "timed_hands.npz"
    source = np.array([180, 250, 413])
    times = np.array([6.001, 8.334, 13.766])
    np.savez(path, names=names, rig_xyz=xyz, frame_idx=[4, 17, 40],
             source_frame_idx=source, timestamps_s=times, time_source="synthetic fixture clock")
    slow = human_grasp_pose_prior("left", 40, hands_npz=path, fps=10, window=2, approach_frames=2)["pose"]
    fast = human_grasp_pose_prior("left", 40, hands_npz=path, fps=60, window=2, approach_frames=2)["pose"]
    assert slow == fast
    assert slow["frame"] == 40 and slow["source_frame"] == 413
    assert slow["preshape_frame"] == 4 and slow["preshape_source_frame"] == 180
    assert slow["capture_time_s"] == 13.766 and slow["preshape_capture_time_s"] == 6.001
    assert slow["retarget_smoothing"] == "disabled_nonuniform_timestamps"
    assert len(slow["finger_targets"]) == 22
    actual = HP.keypoints_from_egodex(path, "left", [40, 4])
    expected = HP.keypoints_from_egodex(FIX / "lego59_left_pick.npz", "left", [contact, contact - 2])
    for key in actual:
        np.testing.assert_array_equal(actual[key], expected[key])
