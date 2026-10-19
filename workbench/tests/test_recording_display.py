"""Display contracts must preserve coordinate identities and recorded timestamps."""
import json

import numpy as np
import pytest

from workbench.native_preview import read_dexagent_recording


def test_recorded_coordinate_display_rejects_identity_and_time_damage(tmp_path):
    report = tmp_path / 'outputs/run_report.json'
    report.parent.mkdir()
    report.write_text(json.dumps({'schema': 'dexagent.stage4_run_report.v1',
                                 'inputs': {'files': {'mesh.obj': 'expected-mesh'}}}))
    path = tmp_path / 'trajectory.npz'
    values = dict(scene_sha256='scene-hash', robot_schema='dexagent.rigid_robot.v1',
        joint_names=['arm', 'finger'], time_s=[0, .2, .4], qpos=[[0, 0], [1, 2], [3, 4]],
        initial_qpos=[0, 0], substep_qpos=[[.1, .2], [1, 2], [2, 3], [3, 4]])
    options = dict(scene_sha256='scene-hash', joint_names=['arm', 'finger'],
                   asset_hashes={'mesh.obj': 'expected-mesh'}, timestep=.1)
    np.savez(path, **values)
    times, positions = read_dexagent_recording(path, **options)
    assert times.tolist() == [0, .2, .4] and positions[-1].tolist() == [3, 4]
    with pytest.raises(ValueError, match='identities'):
        read_dexagent_recording(path, **dict(options, joint_names=['finger', 'arm']))
    with pytest.raises(ValueError, match='mesh/texture'):
        read_dexagent_recording(path, **dict(options, asset_hashes={'mesh.obj': 'changed-mesh'}))
    with pytest.raises(ValueError, match='rigid scene'):
        read_dexagent_recording(path, **dict(options, scene_sha256='different-scene'))
    values['qpos'][1][0] = 999
    np.savez(path, **values)
    with pytest.raises(ValueError, match='timestamps'):
        read_dexagent_recording(path, **options)
