"""Real process lifecycle tests; fixture programs do not certify physics."""

import json
import os
import sys
import subprocess
import threading
import time

from fastapi.testclient import TestClient
import pytest
import yaml

from agent.external import ExternalLLM, atomic_json
from workbench.api import PREFIX, create_app
from workbench.executions import Executions, ExecutionBusy, process_birth
from workbench.index import Index, identity
from workbench.models import ExecutionCreate, TaskVariantInputs
from agent.scene_candidate import digest
from agent.run_reports import sha256_file

PROGRAM = """import sys,time,json,subprocess
from pathlib import Path
import yaml
p=Path(sys.argv[1]) if sys.argv[1].endswith('.yaml') else Path(sys.argv[sys.argv.index('--config')+1])
cfg=yaml.safe_load(p.read_text()); fixture=cfg.get('fixture',{})
out=Path(cfg['run']['output_dir'])/cfg['run']['name'];out.mkdir(parents=True)
print('fixture process ready',flush=True)
if fixture.get('child'):
    child_code="import os,signal,time;signal.signal(signal.SIGINT,signal.SIG_IGN);open("+repr(str(out/'child.pid'))+",'w').write(str(os.getpid()));time.sleep(90)"
    subprocess.Popen([sys.executable,'-c',child_code])
time.sleep(fixture.get('sleep',0))
failed=fixture.get('failed',False)
(out/'stage_report.json').write_text(json.dumps({'schema':'dexagent.step_report.v1','stage':'fixture','status':'failed' if failed else 'passed','checks':[{'id':'fixture_only','status':'failed' if failed else 'passed'}]}))
(out/'run_summary.json').write_text(json.dumps({'run':cfg['run']['name'],'status':'failed' if failed else 'completed','full_pipeline_complete':False,'stages':[]}))
print('fixture exit',fixture.get('exit',0),flush=True)
sys.exit(fixture.get('exit',0))
"""


@pytest.fixture
def workspace(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    for name in ("run_dexagent.py", "run_robosimstudio.py"):
        (repo / name).write_text(PROGRAM)
    config = repo / "dexagent.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "environments": {"pipeline": sys.executable},
                "agent": {"api_key": "private-fixture-value"},
                "fixture": {},
            }
        )
    )
    index = Index(repo, tmp_path / "runs", tmp_path / "state")
    manager = Executions(index, tmp_path / "state")
    try:
        yield manager, config
    finally:
        manager.close()


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.03)
    raise AssertionError("process did not reach the expected state")


def request(manager):
    return ExecutionCreate(config_id=manager.catalog().configs[0].id)


def finished(manager, key):
    return wait_for(
        lambda: (state if (state := manager.state(key)).status != "running" else None)
    )


def configure(config, fixture):
    data = yaml.safe_load(config.read_text())
    data["fixture"] = fixture
    config.write_text(yaml.safe_dump(data))


def test_frozen_config_and_exit_code_do_not_override_native_validation(workspace):
    manager, config = workspace
    configure(config, {"failed": True})
    before = config.read_bytes()
    job = manager.start(request(manager))
    result = finished(manager, job.id)
    assert result.status == "completed" and result.returncode == 0
    assert result.run_id and manager.index.run(result.run_id).validation == "failed"
    assert config.read_bytes() == before
    assert "private-fixture-value" not in result.model_dump_json()
    snapshot = manager.directory(job.id) / "config.yaml"
    assert snapshot.stat().st_mode & 0o777 == 0o600
    effective = yaml.safe_load(snapshot.read_text())
    assert effective["run"]["output_dir"] == str(manager.index.runs_root)
    assert effective["run"]["name"] == job.output_name
    assert "fixture exit" in manager.log(job.id)


def test_retry_reuses_snapshot_and_writes_a_new_output(workspace):
    manager, config = workspace
    configure(config, {"exit": 7})
    first = manager.start(request(manager))
    assert finished(manager, first.id).status == "failed"
    original = (
        manager.index.runs_root / first.output_name / "stage_report.json"
    ).read_bytes()
    configure(config, {"exit": 0})
    retry = manager.retry(first.id)
    assert (
        retry.id != first.id
        and retry.output_name != first.output_name
        and retry.retry_of == first.id
    )
    assert finished(manager, retry.id).returncode == 7
    assert (
        manager.index.runs_root / first.output_name / "stage_report.json"
    ).read_bytes() == original
    snapshot = manager.directory(first.id) / "config.yaml"
    snapshot.write_text("changed after recording")
    with pytest.raises(ValueError, match="快照发生变化"):
        manager.retry(first.id)


def test_restart_preserves_live_identity_and_stop_kills_descendants(workspace):
    manager, config = workspace
    configure(config, {"child": True, "sleep": 90})
    job = manager.start(request(manager))
    child_file = manager.index.runs_root / job.output_name / "child.pid"
    wait_for(child_file.is_file)
    child = int(child_file.read_text())
    run_id = identity(str((manager.index.runs_root / job.output_name).resolve()))
    assert manager.is_run_active(run_id)
    assert not manager.is_run_active("a-different-run")
    restored = Executions(manager.index, manager.root.parent)
    assert restored.is_run_active(run_id)
    assert restored.state(job.id).status == "running"
    with pytest.raises(ExecutionBusy):
        restored.start(request(manager))
    assert restored.stop(job.id).status == "stopped"
    assert not restored.is_run_active(run_id)
    assert process_birth(child) is None
    manager.processes[job.id].wait(timeout=2)
    restored.close()


def test_stale_pid_is_not_killed_and_untrusted_paths_cannot_launch(workspace, tmp_path):
    manager, _ = workspace
    first = manager.start(request(manager))
    finished(manager, first.id)
    data = manager._read(first.id)
    data.update(status="running", pid=os.getpid(), birth="not-this-process-lifetime")
    manager._write(data)
    assert manager.state(first.id).status == "interrupted"
    assert process_birth(os.getpid()) is not None
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"not a video")
    body = request(manager).model_copy(update={"video": str(outside)})
    with pytest.raises(ValueError, match="输入必须位于"):
        manager.start(body)


def test_api_rejects_shell_options_and_retains_launch_failure(workspace, monkeypatch):
    manager, _ = workspace
    app = create_app(
        repo=manager.repo,
        runs_root=manager.index.runs_root,
        state_dir=manager.root.parent,
        refresh_interval=0,
    )
    with TestClient(app) as client:
        body = request(manager).model_dump()
        assert (
            client.post(
                PREFIX + "/executions", json={**body, "command": "touch arbitrary"}
            ).status_code
            == 422
        )
        assert (
            client.post(
                PREFIX + "/executions", json={**body, "mode": "arbitrary"}
            ).status_code
            == 422
        )

        def fail(*args, **kwargs):
            raise OSError("fixture launch failure")

        monkeypatch.setattr("workbench.executions.subprocess.Popen", fail)
        response = client.post(PREFIX + "/executions", json=body)
        assert response.status_code == 202
        result = response.json()
        assert result["status"] == "failed" and result["run_id"] is None
        assert "private-fixture-value" not in response.text
        assert client.get(PREFIX + f"/executions/{result['id']}/log").json() == {
            "text": ""
        }
        assert client.get(PREFIX + "/executions/invalid-id").status_code == 404


@pytest.mark.parametrize("restored", [False, True])
def test_abnormal_parent_exit_reaps_detached_workers(workspace, restored):
    manager, config = workspace
    runner = manager.repo / "run_dexagent.py"
    code = (
        runner.read_text()
        .replace(
            "subprocess.Popen([sys.executable,'-c',child_code])",
            "subprocess.Popen([sys.executable,'-c',child_code],start_new_session=True)",
        )
        .replace("signal.signal(signal.SIGINT,signal.SIG_IGN);", "")
    )
    runner.write_text(code)
    configure(config, {"child": True, "sleep": 0.2})
    unrelated = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"])
    try:
        job = manager.start(request(manager))
        child_path = manager.index.runs_root / job.output_name / "child.pid"
        wait_for(child_path.is_file)
        child = int(child_path.read_text())
        wait_for(lambda: manager.processes[job.id].poll() is not None)
        recovered = (
            Executions(manager.index, manager.root.parent) if restored else manager
        )
        result = recovered.state(job.id)
        assert result.status == "failed" and "子进程" in result.message
        assert process_birth(child) is None
        assert unrelated.poll() is None
        if restored:
            recovered.close()
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=3)


def test_external_agent_wait_is_isolated_and_clears_when_answered(workspace):
    manager, config = workspace
    configure(config, {"sleep": 60})
    before = config.read_bytes()
    job = manager.start(request(manager))
    snapshot = manager.directory(job.id) / "config.yaml"
    transport = manager.index.runs_root / job.output_name / "external"
    assert yaml.safe_load(snapshot.read_text())["agent"]["transport_dir"] == str(
        transport
    )
    assert config.read_bytes() == before
    wait_for(lambda: "fixture process ready" in manager.log(job.id))
    llm = ExternalLLM(transport, timeout_s=5)
    results, errors = [], []

    def call():
        try:
            results.append(llm.complete("test protocol only", [], []))
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=call)
    thread.start()
    try:
        waiting = wait_for(lambda: manager.state(job.id).agent_wait)
        assert waiting.turn == 1 and waiting.request_path.startswith(str(transport))
        assert "等待 Agent" in manager.state(job.id).message
        status_path = transport / "status.json"
        status = json.loads(status_path.read_text())
        status_path.write_text("{broken")
        assert manager.state(job.id).agent_wait is None
        atomic_json(status_path, {**status, "request": "/another/run/request.json"})
        assert manager.state(job.id).agent_wait is None
        atomic_json(status_path, status)
        assert manager.state(job.id).agent_wait is not None
        content = [{"type": "text", "text": "protocol test response"}]
        atomic_json(
            transport / "responses" / (waiting.request_id + ".json"),
            {"session": llm.session, "id": waiting.request_id, "content": content},
        )
        thread.join(timeout=3)
        assert not thread.is_alive() and not errors and results == [content]
        assert manager.state(job.id).agent_wait is None
        assert manager.state(job.id).status == "running"
    finally:
        manager.stop(job.id)
        thread.join(timeout=6)


def test_execution_api_busy_stop_and_retry_have_real_processes(workspace):
    manager, config = workspace
    configure(config, {"sleep": 60})
    with TestClient(
        create_app(
            repo=manager.repo,
            runs_root=manager.index.runs_root,
            state_dir=manager.root.parent,
            refresh_interval=0,
        )
    ) as client:
        body = request(manager).model_dump()
        first = client.post(PREFIX + "/executions", json=body)
        assert first.status_code == 202
        key = first.json()["id"]
        assert client.post(PREFIX + "/executions", json=body).status_code == 409
        assert client.post(PREFIX + f"/executions/{key}/retry").status_code == 409
        assert (
            client.post(PREFIX + f"/executions/{key}/stop").json()["status"]
            == "stopped"
        )
        retry = client.post(PREFIX + f"/executions/{key}/retry")
        assert retry.status_code == 202 and retry.json()["retry_of"] == key
        assert retry.json()["id"] != key
        latest = retry.json()["id"]
        data = client.app.state.executions._read(latest)
    assert process_birth(data["pid"]) is None


def frozen_fixture(directory):
    """An identity fixture only; no claim of physical validity."""
    directory.mkdir(parents=True)
    (directory / "scene.xml").write_text("<mujoco/>")
    manifest = {
        "schema": "embodiforge.scene_candidate.v1",
        "files": {"scene.xml": sha256_file(directory / "scene.xml")},
    }
    manifest["candidate_sha256"] = digest(manifest)
    atomic_json(directory / "manifest.json", manifest)
    return manifest["candidate_sha256"]


def test_candidate_launch_and_retry_bind_the_frozen_version(workspace):
    manager, _ = workspace
    source_run = manager.index.runs_root / "source"
    scene = source_run / "stage3/stage4_task/scene.xml"
    scene.parent.mkdir(parents=True)
    scene.write_text("identity fixture")
    atomic_json(
        source_run / "stage_report.json",
        {"schema": "dexagent.step_report.v1", "status": "passed"},
    )
    manager.index.refresh()
    source_id = manager.index.runs()[0].id
    body = ExecutionCreate(
        config_id=manager.catalog().configs[0].id,
        mode="candidate",
        reference_run_id=source_id,
        scene_sha256="a" * 64,
        scene_offsets=[{"name": "cup", "translation_m": [0.01, 0, 0]}],
    )
    first = manager.start(body)
    finished(manager, first.id)
    first_request = json.loads(
        (manager.directory(first.id) / "scene_request.json").read_text()
    )
    assert first_request["source"] == str(scene)
    assert first_request["offsets"][0]["translation_m"] == [0.01, 0, 0]
    output = manager.index.runs_root / first.output_name
    candidate_sha = frozen_fixture(output / "candidate")
    report_path = output / "stage_report.json"
    report = json.loads(report_path.read_text())
    report["inputs"] = {"candidate": {"sha256": candidate_sha}}
    atomic_json(report_path, report)
    assert manager.state(first.id).candidate_sha256 == candidate_sha
    scene.write_text("changed upstream")
    retry = manager.retry(first.id)
    retry_request = json.loads(
        (manager.directory(retry.id) / "scene_request.json").read_text()
    )
    assert retry_request == {
        "snapshot": str(output / "candidate"),
        "candidate_sha256": candidate_sha,
    }
    finished(manager, retry.id)
    (output / "candidate/scene.xml").write_text("changed candidate")
    with pytest.raises(ValueError, match="身份不匹配"):
        manager.retry(first.id)


def test_candidate_source_must_match_the_native_report(workspace):
    manager, _ = workspace
    output = manager.index.runs_root / "candidate-source"
    frozen_fixture(output / "candidate")
    atomic_json(
        output / "stage_report.json",
        {"schema": "dexagent.step_report.v1", "status": "failed"},
    )
    manager.index.refresh()
    key = manager.index.runs()[0].id
    with pytest.raises(ValueError, match="原生报告"):
        manager.candidate_source(key)
    manifest = json.loads((output / "candidate/manifest.json").read_text())
    atomic_json(
        output / "stage_report.json",
        {
            "schema": "dexagent.step_report.v1",
            "status": "failed",
            "inputs": {"candidate": {"sha256": manifest["candidate_sha256"]}},
        },
    )
    # Failed layouts can be inspected and corrected; they are not promoted.
    assert manager.candidate_source(key) == output / "candidate/scene.xml"


def test_replanning_process_retry_uses_sealed_inputs_not_live_source(
    workspace, monkeypatch
):
    """Substitute only the CLI process; no fixture claims physical acceptance."""
    manager, _ = workspace
    source = manager.index.runs_root / "task-source"
    atomic_json(
        source / "stage_report.json",
        {"schema": "dexagent.step_report.v1", "status": "passed"},
    )
    source_candidate_sha = frozen_fixture(source / "candidate")
    manager.index.refresh()
    source_id = manager.index.runs()[0].id
    runner = manager.repo / "agent/task_variants.py"
    runner.parent.mkdir()
    runner.write_text("# process lifecycle fixture only")
    descriptor = TaskVariantInputs(
        sha256="a" * 64,
        candidate_sha256=source_candidate_sha,
        source_run_id=source_id,
        task="fixture",
    )
    monkeypatch.setattr(manager, "task_inputs", lambda key: descriptor)
    spawn = subprocess.Popen

    def fixture_process(command, **options):
        assert command[1:3] == ["-m", "agent.task_variants"]
        return spawn(
            [sys.executable, str(manager.repo / "run_dexagent.py"), command[3]],
            **options,
        )

    monkeypatch.setattr("workbench.executions.subprocess.Popen", fixture_process)
    body = ExecutionCreate(
        config_id=manager.catalog().configs[0].id,
        mode="replan",
        stop_at="stage3",
        reference_run_id=source_id,
        task_inputs_sha256=descriptor.sha256,
    )
    first = manager.start(body)
    finished(manager, first.id)
    out = manager.index.runs_root / first.output_name
    atomic_json(out / "task_inputs.json", [])
    assert manager.state(first.id).status == "completed"
    assert not manager._read(first.id).get("task_snapshot_sha256")
    candidate_sha = frozen_fixture(out / "candidate")
    assert candidate_sha == source_candidate_sha
    for name in (
        "stage1/scene_semantics.json",
        "stage2/scene.xml",
        "stage2/scene_recon.json",
        "stage2/camera.json",
        "stage2/reference_evidence_recipes.json",
        "inputs/video.mp4",
    ):
        path = out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("identity fixture only")
    atomic_json(
        out / "inputs/source_candidate_report.json",
        {
            "status": "passed",
            "metrics": {"scope": "scene_candidate_validation"},
            "checks": [{"id": "fixture_only", "status": "passed", "required": True}],
            "inputs": {"candidate": {"sha256": candidate_sha}},
        },
    )
    atomic_json(
        out / "inputs/planning_model_identity.json",
        {
            "compiled_physics_equal": True,
            "compiled_model_sha256": "d" * 64,
            "proposal_model_sha256": "c" * 64,
            "planning_scene_sha256": sha256_file(out / "stage2/scene.xml"),
            "candidate_scene_sha256": sha256_file(out / "candidate/scene.xml"),
        },
    )
    files = {
        str(p.relative_to(out)): sha256_file(p)
        for folder in ("stage1", "stage2", "candidate", "inputs")
        for p in (out / folder).rglob("*")
        if p.is_file()
    }
    atomic_json(
        out / "task_inputs.json",
        {
            "schema": "embodiforge.task_variant.v1",
            "files": files,
            "old_controls_reused": False,
            "compiled_model_sha256": "d" * 64,
            "proposal_model_sha256": "c" * 64,
            "input_identity_sha256": descriptor.sha256,
            "candidate_sha256": candidate_sha,
        },
    )
    manager.state(first.id)
    assert manager._read(first.id)["task_snapshot_sha256"] == sha256_file(
        out / "task_inputs.json"
    )
    monkeypatch.setattr(
        manager,
        "task_inputs",
        lambda key: pytest.fail("retry must not inspect live upstream"),
    )
    retry = manager.retry(first.id)
    finished(manager, retry.id)
    config = yaml.safe_load((manager.directory(retry.id) / "config.yaml").read_text())
    assert config["inputs"]["video"] == str(
        manager.index.runs_root / retry.output_name / "inputs/video.mp4"
    )
    assert config["agent"]["transport_dir"] != str(out / "external")
    (out / "task_inputs.json").write_text("changed")
    with pytest.raises(ValueError, match="清单发生变化"):
        manager.retry(first.id)


def test_replanning_resolves_candidate_lineage_and_rejects_changed_origin(workspace):
    manager, _ = workspace
    root = manager.index.runs_root
    source = root / "source-task"
    for name in (
        "stage2/scene.xml",
        "stage2/scene_recon.json",
        "stage2/camera.json",
        "stage2/reference_evidence_recipes.json",
        "stage3/stage4_task/human_video.mp4",
        "stage3/stage4_task/scene.xml",
    ):
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("provenance fixture only")
    atomic_json(
        source / "stage1/scene_semantics.json", {"subgoals": [{}], "task": "fixture"}
    )
    atomic_json(source / "stage_report.json", {"schema": "dexagent.step_report.v1"})
    manager.index.refresh()
    source_id = next(r.id for r in manager.index.runs() if r.name == source.name)

    def candidate(name, origin, parent=None):
        run = root / name
        frozen_fixture(run / "candidate")
        manifest_path = run / "candidate/manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest.pop("candidate_sha256")
        manifest.update(
            source_run_id=origin,
            source={
                "source_sha256": sha256_file(source / "stage3/stage4_task/scene.xml")
            },
            parent_candidate_sha256=parent,
        )
        sha = manifest["candidate_sha256"] = digest(manifest)
        atomic_json(manifest_path, manifest)
        atomic_json(
            run / "stage_report.json",
            {
                "schema": "dexagent.step_report.v1",
                "status": "passed",
                "metrics": {"scope": "scene_candidate_validation"},
                "checks": [
                    {"id": "fixture_only", "required": True, "status": "passed"}
                ],
                "inputs": {"candidate": {"sha256": sha}},
            },
        )
        manager.index.refresh()
        return next(r.id for r in manager.index.runs() if r.name == name), sha

    first_id, first_sha = candidate("first-candidate", source_id)
    last_id, last_sha = candidate("child-candidate", first_id, first_sha)
    descriptor = manager.task_inputs(last_id)
    assert descriptor.source_run_id == source_id
    assert descriptor.candidate_sha256 == last_sha
    (source / "stage3/stage4_task/scene.xml").write_text("replaced source")
    with pytest.raises(ValueError, match="来源场景已变化"):
        manager.task_inputs(last_id)
