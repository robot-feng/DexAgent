"""Partial Conda installs must not enable expensive cleanup phases."""
from __future__ import annotations

import subprocess
import sys

import pytest

from workbench import background_cleanups
from workbench.background_cleanups import BackgroundCleanups, CleanupUnavailable
from workbench.models import CleanupCreate


@pytest.fixture
def manager(tmp_path, monkeypatch):
    envs = tmp_path / 'envs'
    prefix = envs / 'simfoundry'
    (prefix / 'conda-meta').mkdir(parents=True)
    (prefix / 'bin').mkdir()
    (prefix / 'bin/python').symlink_to(sys.executable)
    value = BackgroundCleanups(tmp_path, tmp_path / 'state')
    monkeypatch.setattr(value, '_env_dirs', lambda: [envs])
    monkeypatch.setattr(background_cleanups.shutil, 'which', lambda name: '/usr/bin/ffmpeg' if name == 'ffmpeg' else None)
    return value


def test_created_environment_without_python_is_unavailable(manager):
    (manager._env_dirs()[0] / 'simfoundry/bin/python').unlink()
    ready, detail = manager._environment_ready('simfoundry')
    assert not ready
    assert '缺少 Python 解释器' in detail


def test_missing_dependency_blocks_start_before_job_creation(manager, tmp_path, monkeypatch):
    # Exercise an actual target-Python probe rather than trusting conda-meta.
    monkeypatch.setitem(background_cleanups.ENV_MODULES, 'simfoundry', ('json', 'embodiforge_absent_dependency'))
    auto_bg = manager.simfoundry / 'scripts/pipeline/A_reconstruction/stages/auto_bg_reconstruction'
    auto_bg.mkdir(parents=True)
    (auto_bg / 'run_auto_align.sh').touch()
    source = manager.example_root / 'ThrowAwayTrash.mp4'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'fixture video')
    monkeypatch.setattr(manager, '_video_metadata', lambda path: (1.0, 8, 8))
    monkeypatch.setattr(manager, '_conda', lambda: tmp_path / 'conda')
    video = manager.catalog().videos[0]
    with pytest.raises(CleanupUnavailable, match='embodiforge_absent_dependency'):
        manager.start(CleanupCreate(video_id=video.id, proposal_backend='manual', include_objects=['robot arm'], propagation_model='sam3'))
    assert not manager.catalog().preview_available
    assert not list(manager.jobs_root.iterdir())


def test_missing_environment_is_not_ready(manager):
    ready, detail = manager._environment_ready('void')
    assert not ready
    assert '缺少 void 环境' in detail


@pytest.mark.parametrize('failure', [
    subprocess.TimeoutExpired('python', 5),
    subprocess.CalledProcessError(1, 'python', stderr='a private startup value'),
])
def test_failed_probe_is_unavailable_without_exposing_startup_output(manager, monkeypatch, failure):
    def fail(*args, **kwargs):
        raise failure
    monkeypatch.setattr(background_cleanups.subprocess, 'run', fail)
    ready, detail = manager._environment_ready('simfoundry')
    assert not ready
    assert type(failure).__name__ in detail
    assert 'private startup value' not in detail


@pytest.mark.parametrize('output', ['{}', 'not json', '{"missing": "torch"}', '{"missing": [123]}'])
def test_malformed_probe_result_cannot_mark_environment_ready(manager, monkeypatch, output):
    monkeypatch.setattr(background_cleanups.subprocess, 'run', lambda *args, **kwargs:
                        subprocess.CompletedProcess(args[0], 0, stdout=output, stderr=''))
    ready, detail = manager._environment_ready('simfoundry')
    assert not ready
    assert '检查失败' in detail


def test_cached_catalog_is_rechecked_when_starting_work(manager, tmp_path, monkeypatch):
    module = tmp_path / 'embodiforge_probe_marker.py'
    # Discovery must not execute a package initializer, load weights or start a GPU.
    module.write_text('raise RuntimeError("must not import this module")\n')
    monkeypatch.setenv('PYTHONPATH', str(tmp_path))
    monkeypatch.setitem(background_cleanups.ENV_MODULES, 'simfoundry', ('embodiforge_probe_marker',))
    auto_bg = manager.simfoundry / 'scripts/pipeline/A_reconstruction/stages/auto_bg_reconstruction'
    auto_bg.mkdir(parents=True)
    (auto_bg / 'run_auto_align.sh').touch()
    monkeypatch.setattr(manager, '_conda', lambda: tmp_path / 'conda')

    assert manager.catalog().preview_available
    module.unlink()
    assert manager.catalog().preview_available  # the short-lived UI cache
    with pytest.raises(CleanupUnavailable, match='embodiforge_probe_marker'):
        manager._require(phase='mask', proposal_backend='manual', propagation_model='sam3')
    assert not manager.catalog().preview_available


def test_inpainting_requires_both_environment_dependencies_and_weight_files(manager, monkeypatch):
    void_root = manager.simfoundry / 'deps/void-model'
    inference = void_root / 'inference/cogvideox_fun/inference_with_pass1_warped_noise.py'
    inference.parent.mkdir(parents=True)
    for path in (inference, void_root / 'void_pass1.safetensors', void_root / 'void_pass2.safetensors'):
        path.touch()
    monkeypatch.setattr(manager, '_environment_ready', lambda name: (False, f'{name}: missing torch'))
    requirement = next(item for item in manager._requirements() if item.id == 'void_env')
    assert not requirement.available
    assert 'missing torch' in requirement.detail


def test_custom_conda_environment_roots_are_discovered(tmp_path, monkeypatch):
    envs = tmp_path / 'configured_envs'
    envs.mkdir()
    root = tmp_path / 'mamba_root'
    (root / 'envs').mkdir(parents=True)
    monkeypatch.setenv('CONDA_ENVS_PATH', str(envs))
    monkeypatch.setenv('MAMBA_ROOT_PREFIX', str(root))
    value = BackgroundCleanups(tmp_path, tmp_path / 'state')
    assert value._env_dirs()[:2] == [envs, root / 'envs']


def test_litetext_mask_requires_preprocess_and_its_own_runtime_not_legacy_sam3(tmp_path, monkeypatch):
    from workbench.models import CleanupCatalog, CleanupRequirement

    manager = BackgroundCleanups(tmp_path, tmp_path / 'state')
    names = ('video_tools', 'conda', 'simfoundry_env', 'auto_bg_sources', 'litetext_runtime')
    requirements = [CleanupRequirement(id=name, title=name, available=True, detail='ready') for name in names]
    requirements.extend([
        CleanupRequirement(id='simfoundry_sam3', title='legacy SAM3', available=False, detail='not installed'),
        CleanupRequirement(id='sam2_checkpoint', title='SAM2', available=False, detail='not installed'),
    ])
    monkeypatch.setattr(manager, 'catalog', lambda: CleanupCatalog(
        videos=[], requirements=requirements, preview_available=True, build_available=False))

    manager._require(phase='mask', proposal_backend='manual', propagation_model='litetext')


def test_inpainting_does_not_require_segmentation_runtime_after_mask_review(tmp_path, monkeypatch):
    from workbench.models import CleanupCatalog, CleanupRequirement

    manager = BackgroundCleanups(tmp_path, tmp_path / 'state')
    names = ('video_tools', 'conda', 'simfoundry_env', 'auto_bg_sources', 'void_env')
    requirements = [CleanupRequirement(id=name, title=name, available=True, detail='ready') for name in names]
    requirements.extend([
        CleanupRequirement(id='litetext_runtime', title='LiteText', available=False, detail='removed after review'),
        CleanupRequirement(id='simfoundry_sam3', title='legacy SAM3', available=False, detail='not installed'),
        CleanupRequirement(id='sam2_checkpoint', title='SAM2', available=False, detail='not installed'),
    ])
    monkeypatch.setattr(manager, 'catalog', lambda: CleanupCatalog(
        videos=[], requirements=requirements, preview_available=True, build_available=False))

    manager._require(phase='inpaint', proposal_backend='manual')
