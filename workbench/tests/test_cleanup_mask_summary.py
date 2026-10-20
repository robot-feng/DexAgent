"""Expose actual segmentation metadata to the human mask-review gate."""
from __future__ import annotations

import json

from workbench.background_cleanups import BackgroundCleanups


def _make_review_job(tmp_path):
    manager = BackgroundCleanups(tmp_path, tmp_path / '.workbench')
    job_id = 'a' * 24
    scene = 'review_scene'
    source = tmp_path / 'source.mp4'
    source.touch()
    directory = manager.jobs_root / job_id
    directory.mkdir(parents=True)
    (directory / 'job.json').write_text(json.dumps({
        'id': job_id,
        'scene_name': scene,
        'source_video': {
            'id': 'video', 'name': 'demo.mp4', 'path': 'demo.mp4', 'size': 1,
            'duration_s': 2.0, 'width': 16, 'height': 16, 'capture_type': 'operation_demo',
        },
        'source_copy': str(source),
        'capture_type': 'operation_demo',
        'status': 'review_mask',
        'current_step': 'review',
        'message': 'inspect mask',
        'include_objects': ['gripper'],
        'skip_objects': [],
        'propagation_model': 'litetext',
        'detected_objects': ['gripper'],
        'updated_at': '2026-10-20T00:00:00Z',
    }))
    metadata_path = manager.simfoundry / 'Data' / scene / 'auto_bg/void/input/quadmask_meta.json'
    metadata_path.parent.mkdir(parents=True)
    return manager, job_id, metadata_path


def test_cleanup_job_exposes_litetext_prompt_seed_and_model_metadata(tmp_path):
    manager, job_id, metadata_path = _make_review_job(tmp_path)
    metadata_path.write_text(json.dumps({
        'num_frames': 400,
        'resolution_hw': [384, 672],
        'segmentation_identity': {'model': 'SAM3-LiteText-S0-16'},
        'propagation_identity': {'model': 'sam3-native-video-tracker', 'device': 'cuda:1'},
        'prompt_resolutions': {'gripper': 'end effector'},
        'seed_frame_indices': {'gripper': 12},
        'seed_scores': {'gripper': 0.75},
    }))

    summary = manager.job(job_id).mask_summary

    assert summary is not None
    assert summary.segmentation_model == 'SAM3-LiteText-S0-16'
    assert summary.propagation_model == 'sam3-native-video-tracker'
    assert summary.device == 'cuda:1'
    assert summary.frame_count == 400
    assert summary.resolution_hw == [384, 672]
    assert summary.prompt_resolutions == {'gripper': 'end effector'}
    assert summary.seed_frame_indices == {'gripper': 12}
    assert summary.seed_scores == {'gripper': 0.75}


def test_missing_or_malformed_mask_metadata_does_not_break_review_job(tmp_path):
    manager, job_id, metadata_path = _make_review_job(tmp_path)

    assert manager.job(job_id).mask_summary is None
    metadata_path.write_text('{not json')
    assert manager.job(job_id).mask_summary is None
