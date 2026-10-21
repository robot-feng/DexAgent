"""Publish an existing mask review through the established run/report interfaces."""
import json
from pathlib import Path
import shutil
import requests

from agent.run_reports import atomic_json, make_report, sha256_file
from workbench.background_cleanups import BackgroundCleanups
from workbench.index import identity

repo = Path('/data/tzq/DexAgent')
base = 'http://127.0.0.1:8765/api/workbench/v1'
job_id = 'a81c66712d98408a94c913c8'
manager = BackgroundCleanups(repo, repo / '.workbench')
job = manager.job(job_id)
assert job.status == 'review_mask' and job.run_id is None
assert job.capture_type == 'operation_demo' and job.review_timebase == 'frame_aligned'
run = repo / 'runs/background_separation_review_20261021'
assert not run.exists(), 'publication already exists'
job_dir = manager.directory(job_id)
raw_job = json.loads((job_dir / 'job.json').read_text())
source = Path(raw_job['source_copy'])
mask = manager.media(job_id, 'quadmask')
proxy = manager.media(job_id, 'review-source')
originals = {str(p): sha256_file(p) for p in (source, mask, proxy)}
run.mkdir()
shutil.copy2(source, run / 'input.mp4')
shutil.copy2(job_dir / 'input/review_source.json', run / 'review_source.json')
shutil.copy2(manager.media(job_id, 'mask'), run / 'stage2-preview.png')
atomic_json(run / 'cleanup_source.json', job.model_dump())
atomic_json(run / 'reports/preflight.json', make_report(
    run=run.name, profile='background_cleanup_smoke_test', stage='preflight', status='passed',
    inputs={'video': {'path': str(run / 'input.mp4'), 'sha256': originals[str(source)]}},
    checks=[{'id': 'input_file_identity', 'status': 'passed', 'required': True,
             'observed': sha256_file(run / 'input.mp4'), 'threshold': originals[str(source)]}],
    metrics={'scope': 'input_integrity', 'task_success_evaluated': False}))
checks = [
    {'id': 'mask_output_present', 'status': 'passed', 'required': True, 'observed': originals[str(mask)]},
    {'id': 'shared_frame_clock', 'status': 'passed', 'required': True,
     'observed': {'frames': 400, 'fps': 12, 'max_pts_difference_s': 0}, 'evidence': str(run / 'review_source.json')},
    {'id': 'human_mask_review', 'status': 'skipped', 'required': True,
     'evidence': 'The source job remains review_mask. Neither mask accuracy nor background completion is accepted.'},
    {'id': 'scene_reconstruction', 'status': 'skipped', 'required': False,
     'evidence': 'Operation-demo input qualifies for cleanup smoke testing only; this is not a scene-scan reconstruction.'},
]
atomic_json(run / 'stage2/stage_report.json', make_report(
    run=run.name, profile='background_cleanup_smoke_test', stage='stage2', status='blocked',
    checks=checks, inputs={'cleanup_job_id': job_id, 'capture_type': 'operation_demo',
                          'source': {'path': str(source), 'sha256': originals[str(source)]}},
    model_identity=job.mask_summary.model_dump(),
    artifacts=['cleanup_source.json', 'review_source.json', 'stage2-preview.png'],
    metrics={'scope': 'background_separation_smoke_test', 'task_success_evaluated': False,
             'training_eligible': False, 'aggregate_demo_complete': False},
    next_action='Inspect the full mask sequence before explicitly approving inpainting. No object asset or robot task is accepted.'))
atomic_json(run / 'run_summary.json', {
    'run': run.name, 'profile': 'background_cleanup_smoke_test', 'status': 'blocked',
    'requested_stages': ['stage2'], 'scope_complete': False, 'full_pipeline_complete': False,
})
assert all(sha256_file(Path(path)) == digest for path, digest in originals.items())
run_id = identity(str(run.resolve()))
response = requests.post(base + '/refresh', json={}); response.raise_for_status()
response = requests.post(base + f'/background-cleanups/{job_id}/run', json={'run_id': run_id}); response.raise_for_status()
assert response.json()['run_id'] == run_id and response.json()['status'] == 'review_mask'
detail = requests.get(base + f'/runs/{run_id}').json()
assert detail['validation'] == 'incomplete' and detail['release'] == 'not_evaluated'
result = {'run_id': run_id, 'url': f'http://127.0.0.1:8765/runs/{run_id}?view=workflow&phase=scene&cleanup={job_id}',
          'job_id': job_id, 'validation': detail['validation'], 'source_hashes_unchanged': originals,
          'review_status': 'review_mask', 'physics_steps_added': 0}
atomic_json(repo / '.workbench/previews/reconstruction-results-20261021/publication.json', result)
print(json.dumps(result, ensure_ascii=False))
