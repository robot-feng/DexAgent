"""HumanEgo observations must preserve source time, geometry and missingness."""
import json

import cv2
import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from tool_library.common import rigcam as RC
from tool_library.skills.read_humanego_episode import read_humanego_episode
from tool_library.skills.extract_hand_meshes import _source_frame_indices


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


@pytest.fixture
def session(tmp_path):
    root = tmp_path / "session"
    first = 351521388068186
    write(root / "preprocess/aria_cam_rgb_config.json", {"total_frames": 8, "first_ts": first})
    k = [[120, 0, 32.5], [0, 125, 23.5], [0, 0, 1]]
    world = np.tile([0.2, 0.1, 1.7], (21, 1))
    world[:, 0] += np.arange(21) * .001
    for idx, dt in ((2, 72000000), (7, 241001234)):
        t = np.eye(4)
        t[:3, :3] = Rotation.from_euler("z", idx * 3, degrees=True).as_matrix()
        t[:3, 3] = [idx * .01, 0, .1]
        cam = {"idx": idx, "ts": first + dt, "w": 64, "h": 48, "fps": 30,
               "k": k, "d": [0]*5, "c2w": t.tolist()}
        folder = root / "preprocess/all_data" / f"{idx:05d}"
        write(folder / "aria_cam_rgb.json", cam)
        cv2.imwrite(str(folder / "rgb.png"), np.full((48, 64, 3), idx, np.uint8))
        xyz = (world - t[:3, 3]) @ t[:3, :3]
        uv = xyz[:, :2] / xyz[:, 2:] * [120, 125] + [32.5, 23.5]
        hand = dict(kpts_3d=xyz.tolist(), kpts_2d=uv.tolist(), c2w=t.tolist(), confidence=.9)
        write(folder / "aria_hands.json", {"idx": idx, "ts": first + dt, "hand_r": hand, "hand_l": None})
        write(folder / "training_data.json",
              {"metadata": cam, "obs": {"rgb_path": "/old/machine/annotated.png"},
               "entities": {"objects": {"obj1": {"is_dynamic": False}}}})
    return root, world


def test_sparse_raw_observations_preserve_actual_clock_and_missing_hands(session, tmp_path):
    root, world = session
    result = read_humanego_episode(root, tmp_path / "imported", frames=[7, 2])
    episode = result["episode"]
    assert episode["source_subset"] is True
    assert episode["source_frame_indices"] == {"frame00002.png": 2, "frame00007.png": 7}
    np.testing.assert_array_equal(
        _source_frame_indices(result["frames"][::-1], tmp_path / "imported"), [7, 2])
    assert not episode["training_eligible"]
    assert not episode["sensor_synchronization_verified"]
    assert result["camera"] is None
    assert not (tmp_path / "imported/camera.json").exists()
    with np.load(episode["observations_npz"], allow_pickle=False) as z:
        np.testing.assert_array_equal(z["frame_idx"], [2, 7])
        np.testing.assert_array_equal(z["timestamps_ns"], [351521460068186, 351521629069420])
        np.testing.assert_allclose(z["timestamps_s"], [.072, .241001234], atol=1e-12)
        np.testing.assert_allclose(z["hand_world_xyz"][:, 1], np.stack([world, world]), atol=1e-12)
        assert np.isnan(z["hand_world_xyz"][:, 0]).all()
        assert not z["hand_valid"][:, 0].any()
        assert z["skeleton"].item() == "aria_mps21"
        assert "thumb_cmc" not in z["hand_names"].tolist()
        assert "R_world_rig" not in z
    assert cv2.imread(result["frames"][0])[0, 0].tolist() == [2, 2, 2]
    assert json.loads((tmp_path / "imported/source_records.json").read_text())[0]["training"]["obs"]["rgb_path"].startswith("/old/")


def test_registered_output_reuses_rigcam_with_sparse_ids(session, tmp_path):
    root, world = session
    basis = Rotation.from_euler("xyz", [25, 0, 90], degrees=True).as_matrix()
    origin = np.asarray([.4, -.3, .2])
    reg = tmp_path / "registration.json"
    write(reg, {"R_world_rig": basis.tolist(), "origin_world": origin.tolist(), "source": "synthetic test calibration"})
    result = read_humanego_episode(root, tmp_path / "aligned", frames=[2, 7], registration=reg)
    rotations, positions = RC.camera_poses(result["camera"], [7, 2])
    with np.load(result["episode"]["observations_npz"]) as z:
        np.testing.assert_allclose(z["hand_rig_xyz"][:, 1], np.stack([(world-origin) @ basis]*2))
        for row, idx in enumerate([7, 2]):
            cam = {**result["camera"], "R_cv": rotations[row], "pos": positions[row]}
            uv, _ = RC.project(cam, (world-origin) @ basis)
            np.testing.assert_allclose(uv, z["hand_pixels"][[2, 7].index(idx), 1], atol=1e-10)
    # Saved files stay relocatable; returned dictionary is immediately usable.
    assert json.loads((tmp_path / "aligned/camera.json").read_text())["trajectory_npz"] == "observations.npz"
    assert result["episode"]["registration_status"] == "supplied_unverified"
    with pytest.raises(ValueError, match="missing source frames"):
        RC.camera_poses(result["camera"], [3])


@pytest.mark.parametrize("case", ["clock", "pose", "distortion", "rgb", "training_clock", "escaped_rgb", "scaled_registration"])
def test_invalid_inputs_fail_without_publishing_partial_output(session, tmp_path, case):
    root, _ = session
    folder = root / "preprocess/all_data/00007"
    cam_path = folder / "aria_cam_rgb.json"
    cam = json.loads(cam_path.read_text())
    reg = None
    if case == "clock":
        cam["ts"] = 351521388068186
    elif case == "pose":
        cam["c2w"][0][0] = 3
    elif case == "distortion":
        cam["d"][0] = .1
    elif case == "rgb":
        (folder / "rgb.png").write_bytes(b"not an image")
    elif case == "training_clock":
        path = folder / "training_data.json"
        data = json.loads(path.read_text())
        data["metadata"]["ts"] += 1
        write(path, data)
    elif case == "escaped_rgb":
        outside = tmp_path / "outside.png"
        (folder / "rgb.png").rename(outside)
        (folder / "rgb.png").symlink_to(outside)
    elif case == "scaled_registration":
        reg = tmp_path / "registration.json"
        write(reg, {"R_world_rig": (np.eye(3)*2).tolist(), "origin_world": [0, 0, 0], "source": "invalid scale"})
    write(cam_path, cam)
    with pytest.raises(ValueError):
        read_humanego_episode(root, tmp_path / "invalid", frames=[2, 7], registration=reg)
    assert not (tmp_path / "invalid").exists()


def test_full_import_does_not_silently_accept_sparse_or_overwrite(session, tmp_path):
    root, _ = session
    with pytest.raises(FileNotFoundError):
        read_humanego_episode(root, tmp_path / "full")
    read_humanego_episode(root, tmp_path / "partial", frames=[2])
    with pytest.raises(ValueError, match="new"):
        read_humanego_episode(root, tmp_path / "partial", frames=[2])
    with pytest.raises(ValueError):
        read_humanego_episode(root, tmp_path / "duplicates", frames=[2, 2])
