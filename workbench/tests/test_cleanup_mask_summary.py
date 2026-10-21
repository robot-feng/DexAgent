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


def test_review_source_is_the_processed_frame_sequence_not_the_original_capture(tmp_path):
    manager, job_id, _ = _make_review_job(tmp_path)
    assert manager.job(job_id).review_video_url is None
    prepared = manager.simfoundry / 'Data/review_scene/s1_video/input_video.mp4'
    prepared.parent.mkdir(parents=True)
    prepared.write_bytes(b'same sampled frames as mask')
    job = manager.job(job_id)
    assert job.review_video_url.endswith('/media/review-source')
    assert manager.media(job_id, 'review-source') == prepared.resolve()
    assert manager.media(job_id, 'source') != prepared.resolve()
    assert manager.media(job_id, 'source').read_bytes() == b''


def test_review_proxy_uses_mask_frame_clock_and_rejects_mismatched_inputs(tmp_path):
    import shutil
    import subprocess
    import pytest
    from workbench.background_cleanup_worker import _review_proxy
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('requires CPU video tools')
    manager, job_id, metadata = _make_review_job(tmp_path)
    mask = metadata.parent / 'quadmask_0.mp4'
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=16x16:r=12',
                    '-frames:v', '3', '-c:v', 'libx264', '-threads', '1', '-pix_fmt', 'yuv420p', str(mask)], check=True)
    frames = manager.simfoundry / 'Data/review_scene/s1_video/frames_subsampled_400'
    frames.mkdir(parents=True)
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', str(mask), '-threads', '1', str(frames / '%04d.png')], check=True)
    metadata.write_text(json.dumps({'num_frames': 3}))
    result = _review_proxy(tmp_path, manager.directory(job_id))
    assert result['frame_count'] == 3
    assert result['fps'] == '12'
    assert result['max_pts_difference_s'] <= 1e-6
    assert len(result['frames']) == 3
    assert manager.job(job_id).review_timebase == 'frame_aligned'
    assert manager.job(job_id).review_video_url is not None
    assert manager.media(job_id, 'review-source') == manager.directory(job_id) / 'input/review_source.mp4'
    assert manager.media(job_id, 'source').read_bytes() == b''
    proxy = manager.directory(job_id) / 'input/review_source.mp4'
    proxy.write_bytes(b'changed proxy')
    assert manager.job(job_id).review_timebase is None
    metadata.write_text(json.dumps({'num_frames': 4}))
    with pytest.raises(RuntimeError, match='时间基准不一致'):
        _review_proxy(tmp_path, manager.directory(job_id))
