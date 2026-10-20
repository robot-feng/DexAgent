"""Native trials stay inspectable, including hidden failures, without certification."""
import json

from fastapi.testclient import TestClient
import pytest

from workbench.api import PREFIX, create_app


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


def report(*, ok=False, dry_run=False):
    return {
        'ok': ok, 'dry_run': dry_run, 'n_subgoals': 2,
        'duration_s': 3.5, 'n_frames': 105,
        'subgoals': [
            {'index': 1, 'motion': 'grasp_lift', 'ok': True, 'failed': [], 'frames': [0, 49]},
            {'index': 2, 'motion': 'place', 'ok': ok,
             'failed': [] if ok else ['release_settle', 'hand_penetration'],
             'frames': [50, 104], 'measured': {
                 'max_hand_pen_mm': 3.38,
                 'release_settle': {'moved_since_release_m': .0157,
                                    'issues': ['object moved after release']}}},
        ],
        # Never follow arbitrary paths claimed in a report.
        'files': {'outcomes': '/etc/passwd'},
    }


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / 'runs/example'
    write(root / 'run_summary.json', {'run': 'example', 'status': 'failed'})
    with TestClient(create_app(tmp_path, refresh_interval=0)) as client:
        run_id = client.get(PREFIX + '/runs').json()['items'][0]['id']
        yield client, root, PREFIX + f'/runs/{run_id}'


def test_hidden_trials_are_live_readonly_and_separate_from_acceptance(setup):
    client, root, url = setup
    original = client.get(url).json()
    path = write(root / 'stage3/.candidates/attempt/full_execution/execution_report.json', report())
    before = path.read_bytes()
    result = client.get(url + '/planning-trials').json()
    assert not result['issues'] and not result['truncated']
    item, = result['items']
    assert item['outcome'] == 'failed' and item['passed_subgoals'] == 1
    assert item['recorded_subgoals'] == item['expected_subgoals'] == 2
    detail = client.get(url + '/planning-trials/' + item['report']['id']).json()
    assert detail['subgoals'][1]['measured']['max_hand_pen_mm'] == 3.38
    assert detail['subgoals'][1]['frames'] == [50, 104]
    assert 'files' not in detail
    assert client.get(PREFIX + '/artifacts/' + item['report']['id'] + '/content').content == before
    after = client.get(url).json()
    assert (after['validation'], after['release']) == (original['validation'], original['release'])
    assert client.get(url + '/episodes').json()['items'] == []
    assert path.read_bytes() == before
    write(path, report(ok=True, dry_run=True))
    changed = client.get(url + '/planning-trials').json()['items'][0]
    assert changed['sha256'] != item['sha256']
    assert changed['dry_run'] and changed['outcome'] == 'passed'
    write(path, report(ok=True))
    assert client.get(url + '/planning-trials').json()['items'][0]['outcome'] == 'passed'
    after = client.get(url).json()
    assert (after['validation'], after['release']) == (original['validation'], original['release'])
    assert client.get(url + '/episodes').json()['items'] == []
    path.unlink()
    assert client.get(url + '/planning-trials').json()['items'] == []


@pytest.mark.parametrize('damage', [
    lambda d: d.update(ok='true'),
    lambda d: d.update(n_subgoals=10**12),
    lambda d: d.update(subgoals=[None]),
    lambda d: d['subgoals'][0].update(frames=[10, 2]),
    lambda d: d['subgoals'][0].update(measured={'bad': float('nan')}),
])
def test_corrupt_trial_is_visible_as_issue_not_server_error(setup, damage):
    client, root, url = setup
    d = report()
    damage(d)
    write(root / 'stage3/.candidates/attempt/trial/execution_report.json', d)
    response = client.get(url + '/planning-trials')
    assert response.status_code == 200
    assert not response.json()['items'] and len(response.json()['issues']) == 1


def test_partial_and_contradictory_pass_are_incomplete(setup):
    client, root, url = setup
    d = report(ok=True)
    d['subgoals'].pop()
    write(root / 'stage3/partial/execution_report.json', d)
    d = report(ok=True)
    d['subgoals'][1]['failed'] = ['release_settle']
    write(root / 'stage3/contradiction/execution_report.json', d)
    assert {x['outcome'] for x in client.get(url + '/planning-trials').json()['items']} == {'incomplete'}


def test_run_boundaries_and_symlinks(setup):
    client, root, url = setup
    path = write(root / 'stage3/.candidates/attempt/good/execution_report.json', report())
    report_id = client.get(url + '/planning-trials').json()['items'][0]['report']['id']
    other = root.parent / 'other'
    write(other / 'run_summary.json', {'run': 'other'})
    client.post(PREFIX + '/refresh')
    other_id = next(x['id'] for x in client.get(PREFIX + '/runs').json()['items'] if x['name'] == 'other')
    assert client.get(PREFIX + f'/runs/{other_id}/planning-trials/{report_id}').status_code == 422
    target = write(other / 'stage3/elsewhere/execution_report.json', report(ok=True))
    path.unlink()
    path.symlink_to(target)
    assert not client.get(url + '/planning-trials').json()['items']
    assert client.get(url + '/planning-trials/' + report_id).status_code == 422
    path.unlink()
    path.parent.rmdir()
    path.parent.symlink_to(target.parent, target_is_directory=True)
    assert not client.get(url + '/planning-trials').json()['items']


def test_report_limit_is_explicit(setup, monkeypatch):
    from workbench import diagnostics
    client, root, url = setup
    monkeypatch.setattr(diagnostics, 'MAX_REPORTS', 1)
    for name in ('one', 'two'):
        write(root / f'stage3/{name}/execution_report.json', report())
    result = client.get(url + '/planning-trials').json()
    assert len(result['items']) == 1 and result['truncated']


def test_same_run_report_symlink_swap_rejected(setup):
    client, root, url = setup
    path = write(root / 'stage3/one/execution_report.json', report())
    report_id = client.get(url + '/planning-trials').json()['items'][0]['report']['id']
    target = write(root / 'stage3/two/execution_report.json', report(ok=True))
    path.unlink()
    path.symlink_to(target)
    assert client.get(url + '/planning-trials/' + report_id).status_code == 422
