"""A recorded world placement must survive replay and reject ambiguous input."""

import pytest

from workbench.native_preview import recorded_rig_translation


def manifest(translation=(0.0, 0.0, 0.0)):
    return {
        'schema': 'dexagent.rss_sharpa.v1',
        'rig_to_world_xyzw': [*translation, 0, 0, 0, 1],
        'additional_ground_z_m': translation[2] - 1.0,
    }


@pytest.mark.parametrize('translation', [(0.0, 0.0, 0.0), (0.4, -0.7, 1.0)])
def test_replay_uses_recorded_translation(translation):
    assert recorded_rig_translation(manifest(translation)) == translation


@pytest.mark.parametrize('field,value', [
    ('schema', 'unknown'),
    ('rig_to_world_xyzw', None),
    ('rig_to_world_xyzw', [0, 0, 0]),
    ('rig_to_world_xyzw', [0, 0, float('nan'), 0, 0, 0, 1]),
    ('rig_to_world_xyzw', [0, 0, 0, 0, 0, 0, 0]),
    ('rig_to_world_xyzw', [0, 0, 0, 0, 0, .5, .8660254]),
    ('rig_to_world_xyzw', [True, 0, 0, 0, 0, 0, 1]),
    ('additional_ground_z_m', 0),
    ('additional_ground_z_m', float('inf')),
])
def test_replay_rejects_unsupported_or_inconsistent_frames(field, value):
    data = manifest()
    data[field] = value
    with pytest.raises(ValueError):
        recorded_rig_translation(data)
