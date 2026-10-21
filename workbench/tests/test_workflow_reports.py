"""Optional provider stages use the established report contract and preserve failures."""
import json

from agent.run_reports import make_report
from workbench.index import Index


def test_optional_stage_discovery_refresh_and_report_validation(tmp_path):
    # A provider-only run must be discoverable without a legacy stage1–4 report.
    root = tmp_path / 'runs/provider-run'
    stage = root / 'training'
    stage.mkdir(parents=True)
    path = stage / 'stage_report.json'
    report = make_report(run='provider-run', profile='training', stage='training', status='passed',
                         checks=[{'id': 'checkpoint', 'status': 'passed', 'required': True}])
    report.update(scope='policy_training', task_success_evaluated=False)
    path.write_text(json.dumps(report))
    index = Index(tmp_path, tmp_path / "runs", tmp_path / "state")
    index.refresh()
    run = index.runs()[0]
    detail = index.run(run.id)
    assert [s.id for s in detail.stages] == ['training']
    assert detail.stages[0].report.evaluation_scope == 'policy_training'
    assert detail.stages[0].report.task_success_evaluated is False
    assert detail.release == 'not_evaluated'
    assert detail.scope.single_invocation_full_pipeline_complete is None
    # Replacement provider failure refreshes the cached summary, not just detail.
    report['status'] = 'failed'
    report['checks'][0]['status'] = 'failed'
    path.write_text(json.dumps(report))
    index.refresh()
    assert index.runs()[0].validation == 'failed'
    # Unrecognized provider JSON is visible but never considered acceptance.
    for name in ('reference', 'execution', 'evaluation'):
        target = root / name / 'stage_report.json'
        target.parent.mkdir()
        target.write_text(json.dumps({'schema': 'vendor.custom.v1', 'status': 'passed'}))
    index.refresh()
    detail = index.run(run.id)
    assert len(detail.stages) == 4
    assert all(s.report.validation == 'incomplete' for s in detail.stages if s.id != 'training')
    assert path.read_text() == json.dumps(report)


def test_report_provenance_is_projected_without_changing_acceptance(tmp_path):
    root = tmp_path / 'runs/provenance-run/training'
    root.mkdir(parents=True)
    report = make_report(run='provenance-run', profile='training', stage='training', status='passed',
                         checks=[{'id': 'checkpoint', 'status': 'passed', 'required': True}])
    report['inputs'] = {
        'dataset': {'path': 'episodes/', 'sha256': 'logical-dataset-identity'},
        'agent': {
            'api_key': 'should-not-leak',
            'nested': [{'access_token': 'also-secret', 'secret_access_key': 'and-this'}],
        },
    }
    report['model_identity'] = {'policy': {'backend': 'example-policy', 'revision': 'abc123'}}
    report['config_sha256'] = 'effective-config-identity'
    report['artifacts'] = ['checkpoint: runs/provenance-run/training/model.pt',
                           'metrics: runs/provenance-run/training/metrics.json']
    path = root / 'stage_report.json'
    path.write_text(json.dumps(report))
    index = Index(tmp_path, tmp_path / 'runs', tmp_path / 'state')
    index.refresh()
    projected = index.run(index.runs()[0].id).stages[0].report
    assert projected.validation == 'passed'
    assert projected.inputs == {
        'dataset': {'path': 'episodes/', 'sha256': 'logical-dataset-identity'},
        'agent': {
            'api_key': '[REDACTED]',
            'nested': [{'access_token': '[REDACTED]', 'secret_access_key': '[REDACTED]'}],
        },
    }
    assert projected.model_identity == report['model_identity']
    assert projected.config_sha256 == report['config_sha256']
    assert projected.declared_artifacts == report['artifacts']
    assert not projected.provenance_issues

    report['inputs'] = ['malformed legacy value']
    report['model_identity'] = None
    report['config_sha256'] = 123
    report['artifacts'] = ['kept as text', {'path': 'not interpreted'}]
    path.write_text(json.dumps(report))
    index.refresh()
    projected = index.run(index.runs()[0].id).stages[0].report
    assert projected.validation == 'passed'
    assert projected.inputs == {}
    assert projected.model_identity == {}
    assert projected.config_sha256 is None
    assert projected.declared_artifacts == ['kept as text']
    assert len(projected.provenance_issues) == 3


def test_artifact_folder_is_owned_by_run_not_a_substring(tmp_path):
    from fastapi.testclient import TestClient
    from workbench.api import PREFIX, create_app
    # Name deliberately matches a stage to catch substring-based ownership.
    root = tmp_path / 'runs/stage2'
    for relative in ('stage2/frame.png', 'training/loss.png', 'preview.png'):
        file = root / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b'preview')
    (root / 'stage_report.json').write_text(json.dumps(make_report(
        run='stage2', profile='backend_eval', stage='backend', status='passed',
        checks=[{'id': 'check', 'status': 'passed', 'required': True}])))
    with TestClient(create_app(tmp_path, refresh_interval=0)) as client:
        run_id = client.get(PREFIX + '/runs').json()['items'][0]['id']
        endpoint = PREFIX + f'/runs/{run_id}/artifacts'
        def query(folder):
            return client.get(endpoint, params={'folder': folder, 'kind': 'image', 'limit': 1}).json()
        assert query('stage2')['total'] == 1
        assert query('stage2')['items'][0]['name'] == 'frame.png'
        assert query('')['total'] == 1
        assert query('')['items'][0]['name'] == 'preview.png'
        assert query('missing')['total'] == 0
        for invalid in ('../training', '/training', 'training/../stage2', 'training\\loss.png'):
            assert client.get(endpoint, params={'folder': invalid}).status_code == 400
