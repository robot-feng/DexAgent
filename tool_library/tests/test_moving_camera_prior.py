"""Camera-motion compensation uses source frame identities and preserves rig geometry."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from tool_library.common import human_prior as HP
from tool_library.common import rigcam as RC
from tool_library.skills.extract_hand_meshes import _source_frame_indices
from tool_library.skills.place_egodex_camera_for_reach import _reframe
from tool_library.skills.read_egodex_episode import read_egodex_episode


@pytest.fixture
def capture(tmp_path):
    """One stationary hand observed from three independently specified moving cameras."""
    ids = np.array([10, 25, 60])
    rotations = Rotation.from_euler(
        "xyz", [[5, -12, 18], [-8, 17, -21], [14, 6, 31]], degrees=True
    ).as_matrix()
    positions = np.array([[0.1, -0.4, 0.7], [-0.2, -0.25, 0.8], [0.35, -0.1, 0.6]])
    basis = Rotation.from_euler("xyz", [21, -15, 43], degrees=True).as_matrix()
    origin = np.array([2.0, -3.0, 1.5])
    world = np.repeat(np.eye(4)[None], len(ids), axis=0)
    world[:, :3, :3] = basis @ rotations
    world[:, :3, 3] = positions @ basis.T + origin
    rig_vertices = np.random.default_rng(71).normal(scale=0.035, size=(778, 3))
    rig_vertices += [0.25, 0.35, 1.9]
    # The independent forward-observation oracle uses world points and the
    # inverse homogeneous camera pose, rather than RC.camera_poses.
    world_vertices = rig_vertices @ basis.T + origin
    vertices_cv = np.stack([
        (world_vertices - pose[:3, 3]) @ pose[:3, :3] for pose in world
    ])
    regressor = np.zeros((16, 778))
    regressor[np.arange(16), np.arange(16)] = 1
    archive = {
        "camera_world": world,
        "R_world_rig": basis,
        "origin_world": origin,
        "frame_idx": ids,
        "rig_xyz": np.repeat(rig_vertices[None, :16], len(ids), axis=0),
    }
    trajectory = tmp_path / "hands.npz"
    np.savez_compressed(trajectory, **archive)
    hamer = tmp_path / "hamer.npz"
    np.savez_compressed(
        hamer, frame_idx=ids, source_frame_idx=ids, is_right=np.ones((len(ids), 1), dtype=int),
        verts=vertices_cv[:, None], cam_t=np.zeros((len(ids), 1, 3)),
    )
    camera = RC.camera_dict(
        640, 480, 520, 520, 320, 240, rotations[1], positions[1],
        {"camera_motion": "moving", "trajectory_npz": str(trajectory), "reference_frame": 25},
    )
    camera_path = tmp_path / "camera.json"
    RC.save(camera, camera_path)
    return {
        "ids": ids, "rotations": rotations, "positions": positions,
        "archive": archive, "trajectory": trajectory, "camera": camera,
        "camera_path": camera_path, "hamer": hamer, "regressor": regressor,
        "vertices": rig_vertices, "vertices_cv": vertices_cv,
    }


def _expected_keypoints(vertices):
    """Explicit MANO vertex/joint indexing, independent of production constants."""
    return {
        "wrist": vertices[0],
        "thumb": vertices[[13, 14, 15, 745]],
        "index": vertices[[1, 2, 3, 317]],
        "middle": vertices[[4, 5, 6, 444]],
        "pinky": vertices[[7, 8, 9, 673]],
        "ring": vertices[[10, 11, 12, 556]],
    }


def test_moving_camera_recovers_stationary_hand_and_exact_sparse_frame_ids(capture):
    requested = [60, 10, 25, 60]
    rotations, positions = RC.camera_poses(capture["camera"], requested)
    np.testing.assert_allclose(rotations, capture["rotations"][[2, 0, 1, 2]], atol=1e-12)
    np.testing.assert_allclose(positions, capture["positions"][[2, 0, 1, 2]], atol=1e-12)
    actual = HP.keypoints_from_hamer(
        capture["hamer"], capture["camera_path"], "right", requested,
        capture["regressor"], hamer_focal_px=520,
    )
    for name, expected in _expected_keypoints(capture["vertices"]).items():
        np.testing.assert_allclose(actual[name], np.repeat(expected[None], 4, axis=0), atol=1e-12)


def test_legacy_static_camera_keeps_previous_projection_behavior(capture):
    with np.load(capture["hamer"], allow_pickle=False) as archive:
        legacy = {key: archive[key] for key in archive.files if key != "source_frame_idx"}
    np.savez_compressed(capture["hamer"], **legacy)
    static = {key: value for key, value in capture["camera"].items()
              if key not in ("trajectory_npz", "camera_motion", "reference_frame")}
    rotations, positions = RC.camera_poses(static, [60, 10])
    np.testing.assert_array_equal(rotations, np.repeat(capture["rotations"][None, 1], 2, axis=0))
    np.testing.assert_array_equal(positions, np.repeat(capture["positions"][None, 1], 2, axis=0))
    actual = HP.keypoints_from_hamer(
        capture["hamer"], static, "right", [60, 10], capture["regressor"], hamer_focal_px=520,
    )
    wrong_rig = [
        capture["vertices_cv"][row] @ capture["rotations"][1].T + capture["positions"][1]
        for row in (2, 0)
    ]
    for name in actual:
        expected = np.array([_expected_keypoints(vertices)[name] for vertices in wrong_rig])
        np.testing.assert_allclose(actual[name], expected, atol=1e-12)
    assert np.linalg.norm(actual["wrist"][0] - capture["vertices"][0]) > 0.1


def test_producer_mapping_keeps_hamer_ordinals_distinct_from_source_video_ids(capture, tmp_path):
    files = [tmp_path / f"frame{frame:05d}.jpg" for frame in capture["ids"]]
    for path in files:
        path.touch()
    mapping = {path.name: int(frame) for path, frame in zip(files, capture["ids"])}
    (tmp_path / "episode.json").write_text(json.dumps({"source_frame_indices": mapping}))
    source_ids = _source_frame_indices(files, tmp_path)
    np.testing.assert_array_equal(source_ids, [10, 25, 60])
    # This is the actual producer's index contract: frame_idx is image ordinal,
    # even when the selected source images came from distant video frames.
    with np.load(capture["hamer"], allow_pickle=False) as archive:
        output = {key: archive[key] for key in archive.files}
    output.update(frame_idx=np.array([0, 1, 2]), source_frame_idx=source_ids)
    np.savez_compressed(capture["hamer"], **output)
    actual = HP.keypoints_from_hamer(
        capture["hamer"], capture["camera_path"], "right", [2, 0, 1],
        capture["regressor"], hamer_focal_px=520,
    )
    for name, expected in _expected_keypoints(capture["vertices"]).items():
        np.testing.assert_allclose(actual[name], np.repeat(expected[None], 3, axis=0), atol=1e-12)
    with pytest.raises(ValueError, match="not in the HaMeR output"):
        HP.keypoints_from_hamer(
            capture["hamer"], capture["camera_path"], "right", [60],
            capture["regressor"], hamer_focal_px=520,
        )


@pytest.mark.parametrize("mapping", [
    [10, 25, 60],
    {"frame00010.jpg": 10},
    {"frame00010.jpg": 10, "frame00025.jpg": 25, "frame00060.jpg": 25},
    {"frame00010.jpg": 10.0, "frame00025.jpg": 25, "frame00060.jpg": 60},
    {"frame00010.jpg": True, "frame00025.jpg": 25, "frame00060.jpg": 60},
    {"frame00010.jpg": -1, "frame00025.jpg": 25, "frame00060.jpg": 60},
])
def test_producer_rejects_malformed_source_mapping(tmp_path, mapping):
    files = [tmp_path / f"frame{frame:05d}.jpg" for frame in (10, 25, 60)]
    (tmp_path / "episode.json").write_text(json.dumps({"source_frame_indices": mapping}))
    with pytest.raises(ValueError):
        _source_frame_indices(files, tmp_path)


def test_producer_does_not_infer_source_ids_from_filenames_without_explicit_mapping(tmp_path):
    files = [tmp_path / "frame00060.jpg", tmp_path / "frame00010.jpg"]
    assert _source_frame_indices(files, tmp_path) is None
    (tmp_path / "episode.json").write_text(json.dumps({"fps": 30}))
    assert _source_frame_indices(files, tmp_path) is None


@pytest.mark.parametrize("source_ids", [
    None, [10, 25], [[10, 25, 60]], [10, 25, 25],
    [10.0, 25.0, 60.0], [True, False, True], [-1, 25, 60],
])
def test_moving_hamer_requires_valid_explicit_source_mapping(capture, source_ids):
    with np.load(capture["hamer"], allow_pickle=False) as archive:
        output = {key: archive[key] for key in archive.files if key != "source_frame_idx"}
    if source_ids is not None:
        output["source_frame_idx"] = np.array(source_ids)
    np.savez_compressed(capture["hamer"], **output)
    with pytest.raises(ValueError, match="source_frame_idx"):
        HP.keypoints_from_hamer(
            capture["hamer"], capture["camera_path"], "right", [25],
            capture["regressor"], hamer_focal_px=520,
        )


def test_trajectory_without_frame_idx_uses_zero_based_source_frames(capture):
    archive = {key: value for key, value in capture["archive"].items() if key != "frame_idx"}
    np.savez_compressed(capture["trajectory"], **archive)
    camera = dict(capture["camera"], reference_frame=1)
    rotations, positions = RC.camera_poses(camera, [2, 0])
    np.testing.assert_allclose(rotations, capture["rotations"][[2, 0]], atol=1e-12)
    np.testing.assert_allclose(positions, capture["positions"][[2, 0]], atol=1e-12)


def test_relative_trajectory_resolves_from_camera_file_not_working_directory(capture, tmp_path, monkeypatch):
    camera = dict(capture["camera"], trajectory_npz="hands.npz")
    RC.save(camera, capture["camera_path"])
    elsewhere = tmp_path / "unrelated"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    loaded = RC.load(capture["camera_path"])
    assert Path(loaded["trajectory_npz"]) == capture["trajectory"]
    rotations, positions = RC.camera_poses(loaded, [60])
    np.testing.assert_allclose(rotations[0], capture["rotations"][2], atol=1e-12)
    np.testing.assert_allclose(positions[0], capture["positions"][2], atol=1e-12)


@pytest.mark.parametrize("frames", [[0], [24], [61], [-1], [10.5], [True], [[10]]])
def test_missing_or_invalid_frame_requests_are_not_silently_reindexed(capture, frames):
    with pytest.raises(ValueError):
        RC.camera_poses(capture["camera"], frames)


def test_declared_moving_camera_requires_existing_trajectory(capture):
    missing = dict(capture["camera"])
    del missing["trajectory_npz"]
    with pytest.raises(ValueError):
        RC.camera_poses(missing, [25])
    missing["trajectory_npz"] = str(capture["trajectory"].with_name("missing.npz"))
    with pytest.raises(FileNotFoundError):
        RC.camera_poses(missing, [25])


@pytest.mark.parametrize("fault", [
    "missing_camera_world", "nonfinite_camera", "nonfinite_origin",
    "reflection_camera", "reflection_basis", "scaled_camera", "invalid_bottom",
    "wrong_camera_shape", "duplicate_ids", "unsorted_ids", "float_ids",
    "wrong_id_count", "negative_ids",
])
def test_invalid_trajectory_is_rejected(capture, fault):
    archive = {key: value.copy() for key, value in capture["archive"].items()}
    if fault == "missing_camera_world":
        del archive["camera_world"]
    elif fault == "nonfinite_camera":
        archive["camera_world"][2, 0, 3] = np.nan
    elif fault == "nonfinite_origin":
        archive["origin_world"][0] = np.inf
    elif fault == "reflection_camera":
        archive["camera_world"][2, :3, 0] *= -1
    elif fault == "reflection_basis":
        archive["R_world_rig"][:, 0] *= -1
    elif fault == "scaled_camera":
        archive["camera_world"][2, :3, :3] *= 2
    elif fault == "invalid_bottom":
        archive["camera_world"][2, 3, 0] = 0.1
    elif fault == "wrong_camera_shape":
        archive["camera_world"] = archive["camera_world"][:, :3]
    elif fault == "duplicate_ids":
        archive["frame_idx"] = np.array([10, 25, 25])
    elif fault == "unsorted_ids":
        archive["frame_idx"] = np.array([10, 60, 25])
    elif fault == "float_ids":
        archive["frame_idx"] = np.array([10.0, 25.0, 60.0])
    elif fault == "wrong_id_count":
        archive["frame_idx"] = np.array([10, 25])
    elif fault == "negative_ids":
        archive["frame_idx"] = np.array([-1, 25, 60])
    np.savez_compressed(capture["trajectory"], **archive)
    with pytest.raises(ValueError):
        RC.camera_poses(capture["camera"], [25])


@pytest.mark.parametrize("fault", ["position", "rotation", "missing_frame", "fractional_frame"])
def test_reference_pose_must_match_trajectory(capture, fault):
    camera = dict(capture["camera"])
    if fault == "position":
        camera["pos"] = (np.array(camera["pos"]) + [0.01, 0, 0]).tolist()
    elif fault == "rotation":
        camera["R_cv"] = capture["rotations"][0].tolist()
    elif fault == "missing_frame":
        camera["reference_frame"] = 26
    else:
        camera["reference_frame"] = 25.5
    with pytest.raises(ValueError):
        RC.camera_poses(camera, [60])


def test_reframe_moves_all_camera_poses_and_hand_points_together(capture, tmp_path):
    source_bytes = capture["trajectory"].read_bytes()
    (tmp_path / "episode.json").write_text(json.dumps({
        "rig": {"R_world_rig": capture["archive"]["R_world_rig"].tolist(),
                "origin_world": capture["archive"]["origin_world"].tolist()},
    }))
    destination = tmp_path / "reframed"
    destination.mkdir()
    transform = {"dx": 0.21, "dy": -0.13, "dyaw_deg": 37, "about": [0.2, -0.3]}
    files = _reframe(tmp_path, destination, transform, [0.41, -0.43])
    camera = RC.load(files["camera_json"])
    assert Path(camera["trajectory_npz"]) == destination / "hands.npz"
    assert camera["reference_frame"] == 25
    assert camera["camera_motion"] == "moving"
    yaw = Rotation.from_euler("z", 37, degrees=True).as_matrix()
    about = np.array([0.2, -0.3, 0.0])
    shift = np.array([0.21, -0.13, 0.0])
    expected_vertices = (capture["vertices"] - about) @ yaw.T + about + shift
    rotations, positions = RC.camera_poses(camera, [10, 25, 60])
    np.testing.assert_allclose(rotations, yaw @ capture["rotations"], atol=1e-12)
    np.testing.assert_allclose(
        positions, (capture["positions"] - about) @ yaw.T + about + shift, atol=1e-12,
    )
    with np.load(files["hands_npz"], allow_pickle=False) as archive:
        np.testing.assert_array_equal(archive["camera_world"], capture["archive"]["camera_world"])
        np.testing.assert_allclose(
            archive["rig_xyz"], np.repeat(expected_vertices[None, :16], 3, axis=0), atol=1e-7,
        )
    actual = HP.keypoints_from_hamer(
        capture["hamer"], files["camera_json"], "right", [10, 25, 60],
        capture["regressor"], hamer_focal_px=520,
    )
    for name, expected in _expected_keypoints(expected_vertices).items():
        np.testing.assert_allclose(actual[name], np.repeat(expected[None], 3, axis=0), atol=1e-12)
    assert capture["trajectory"].read_bytes() == source_bytes
    episode = json.loads(Path(files["episode_json"]).read_text())
    assert episode["camera_json"] == files["camera_json"]
    assert episode["hands_npz"] == files["hands_npz"]


def test_reframe_rejects_unrelated_trajectory(capture, tmp_path):
    camera = dict(capture["camera"], trajectory_npz=str(tmp_path / "another_capture.npz"))
    RC.save(camera, capture["camera_path"])
    destination = tmp_path / "reframed"
    destination.mkdir()
    with pytest.raises(ValueError):
        _reframe(
            tmp_path, destination, {"dx": 0, "dy": 0, "dyaw_deg": 0, "about": [0, 0]}, [0, 0],
        )


def _write_egodex_source(capture, directory, video_frames):
    import cv2
    import h5py

    hdf5 = directory / "source.hdf5"
    video = hdf5.with_suffix(".mp4")
    archive = capture["archive"]
    tip_world = capture["vertices"][317] @ archive["R_world_rig"].T + archive["origin_world"]
    fingertip = np.repeat(np.eye(4)[None], 3, axis=0)
    fingertip[:, :3, 3] = tip_world
    with h5py.File(hdf5, "w") as source:
        source.attrs["llm_description"] = "Hold the hand stationary while moving the camera."
        source.attrs["task"] = "camera_motion_fixture"
        source.create_dataset("camera/intrinsic", data=[[52, 0, 32], [0, 52, 24], [0, 0, 1]])
        source.create_dataset("transforms/camera", data=archive["camera_world"])
        source.create_dataset("transforms/rightIndexFingerTip", data=fingertip)
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), 15, (64, 48))
    assert writer.isOpened(), "This integration test requires a working OpenCV MP4 writer"
    try:
        for frame in range(video_frames):
            image = np.full((48, 64, 3), 40 + frame * 50, dtype=np.uint8)
            writer.write(image)
    finally:
        writer.release()
    return hdf5, video, tip_world


def test_egodex_reader_emits_usable_moving_track_and_nominal_frame_times(capture, tmp_path):
    hdf5, video, tip_world = _write_egodex_source(capture, tmp_path, video_frames=3)
    output = tmp_path / "read"
    result = read_egodex_episode(hdf5, output, mp4=video, frame=1, stride=1)
    camera = RC.load(output / "camera.json")
    assert camera["camera_motion"] == "moving"
    assert camera["reference_frame"] == 1
    assert Path(camera["trajectory_npz"]) == output / "hands.npz"
    assert [Path(path).name for path in result["frames"]] == [
        "frame00001.jpg", "frame00000.jpg", "frame00002.jpg",
    ]
    assert result["episode"]["source_frame_indices"] == {
        "frame00001.jpg": 1, "frame00000.jpg": 0, "frame00002.jpg": 2,
    }
    rotations, positions = RC.camera_poses(camera, [2, 0, 1])
    with np.load(output / "hands.npz", allow_pickle=False) as archive:
        basis, origin = archive["R_world_rig"], archive["origin_world"]
        np.testing.assert_array_equal(archive["frame_idx"], [0, 1, 2])
        np.testing.assert_allclose(archive["timestamps_s"], np.array([0, 1, 2]) / 15)
        np.testing.assert_allclose(
            basis @ rotations, capture["archive"]["camera_world"][[2, 0, 1], :3, :3], atol=1e-7,
        )
        np.testing.assert_allclose(
            positions @ basis.T + origin,
            capture["archive"]["camera_world"][[2, 0, 1], :3, 3], atol=2e-7,
        )
        np.testing.assert_allclose(
            archive["rig_xyz"][:, 0] @ basis.T + origin,
            np.repeat(tip_world[None], 3, axis=0), atol=2e-7,
        )
        for frame, pose in enumerate(capture["archive"]["camera_world"]):
            point_cv = (tip_world - pose[:3, 3]) @ pose[:3, :3]
            expected_pixel = [32 + 52 * point_cv[0] / point_cv[2], 24 + 52 * point_cv[1] / point_cv[2]]
            np.testing.assert_allclose(archive["pixels"][frame, 0], expected_pixel, atol=1e-5)
    assert "not measured capture timestamps" in result["episode"]["time_source"]


def test_egodex_reader_rejects_video_pose_count_mismatch(capture, tmp_path):
    hdf5, video, _ = _write_egodex_source(capture, tmp_path, video_frames=2)
    output = tmp_path / "read"
    with pytest.raises(ValueError, match="one frame per camera pose"):
        read_egodex_episode(hdf5, output, mp4=video)
    assert not (output / "camera.json").exists()
    assert not (output / "hands.npz").exists()
