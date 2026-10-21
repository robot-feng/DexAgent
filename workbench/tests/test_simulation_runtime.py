"""Selected execution interpreters, dependency constraints and failure semantics."""
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest

from workbench.api import PREFIX, create_app
from workbench.simulation_runtime import inspect_runtime, interpret_probe


def metadata(version="1.6.0"):
    versions = {"newton": version, "warp-lang": "1.18.0", "mujoco": "3.12.0",
                "mujoco-warp": "3.12.0", "robosimstudio": "0.1.0",
                "numpy": "1.26.4", "scipy": "1.17.0", "viser": "1.1.1"}
    return dict(packages=[dict(name=k, version=v, available=v is not None)
                          for k, v in versions.items()],
                requirements=["newton[sim]>=1.6,<1.7"])


def config(tmp_path, python=sys.executable, extra=""):
    path = tmp_path / "runtime.yaml"
    path.write_text(f"environments:\n  pipeline: {json.dumps(str(python))}\n{extra}")
    return {"a" * 24: path}


def test_selected_interpreter_used_by_api(tmp_path):
    # An actual subprocess reports a distinct environment; never inspect the API
    # interpreter instead or import its physics packages as a side effect.
    executable = tmp_path / "worker-python"
    executable.write_text(f"#!{sys.executable}\nprint({json.dumps(metadata())!r})\n")
    executable.chmod(0o755)
    config(tmp_path, executable)
    before = set(sys.modules)
    with TestClient(create_app(tmp_path, refresh_interval=0)) as client:
        catalog = client.get(PREFIX + "/executions/catalog").json()
        identifier = catalog["configs"][0]["id"]
        response = client.get(PREFIX + "/simulation/runtime", params={"config_id": identifier})
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "environment_detected"
        assert body["python"] == str(executable)
        assert body["packages"][0]["version"] == "1.6.0"
        assert "尚未" in body["detail"]
        assert client.get(PREFIX + "/simulation/runtime",
                          params={"config_id": "f" * 24}).status_code == 422
        assert client.get(PREFIX + "/simulation/runtime",
                          params={"config_id": "/tmp/python"}).status_code == 422
    assert not ({"newton", "warp", "mujoco"} & (set(sys.modules) - before))


def test_dependency_version_and_missing_are_not_success():
    assert interpret_probe(metadata())[0] == "environment_detected"
    assert interpret_probe(metadata("1.7.0"))[0] == "incompatible"
    assert interpret_probe(metadata(None))[0] == "missing_dependencies"
    data = metadata()
    data["requirements"] = []
    assert interpret_probe(data)[0] == "error"
    data["packages"].pop()
    with pytest.raises(ValueError):
        interpret_probe(data)


@pytest.mark.parametrize("failure", ["exit", "json", "timeout", "missing"])
def test_probe_failure_is_explicit_and_does_not_expose_startup_secrets(tmp_path, failure):
    configs = config(tmp_path)
    result = subprocess.CompletedProcess([], 1 if failure == "exit" else 0,
                                         "TOKEN=private-example", "TOKEN=private-example")
    error = (subprocess.TimeoutExpired("python", 10) if failure == "timeout"
             else FileNotFoundError("TOKEN=private-example") if failure == "missing" else None)
    with patch("workbench.simulation_runtime.subprocess.run", return_value=result, side_effect=error):
        response = inspect_runtime(tmp_path, configs)
    assert response.status == "error"
    assert "private-example" not in response.model_dump_json()


def test_invalid_yaml_and_physics_device_have_readable_errors(tmp_path):
    configs = config(tmp_path, extra="evaluation: [broken")
    assert inspect_runtime(tmp_path, configs).status == "error"
    configs = config(tmp_path, extra="evaluation:\n  robosimstudio:\n    grasp:\n      physics_device: magic\n")
    assert inspect_runtime(tmp_path, configs).status == "error"


def test_configured_cpu_and_gpu_physics_remain_distinct(tmp_path):
    configs = config(tmp_path, extra="evaluation:\n  robosimstudio:\n    grasp:\n      physics_device: gpu\n")
    result = subprocess.CompletedProcess([], 0, json.dumps(metadata()), "")
    with patch("workbench.simulation_runtime.subprocess.run", return_value=result):
        response = inspect_runtime(tmp_path, configs)
    profiles = {p.mode: p for p in response.profiles}
    assert profiles["grasp"].physics_device == "gpu"
    assert profiles["reference"].physics_device == "gpu"
    assert profiles["candidate"].physics_device == "cpu"
    assert profiles["simfoundry"].physics_device == "cpu"
    assert "状态与渲染" in profiles["candidate"].detail
    assert "pipeline" not in profiles
    assert "replan" not in profiles
    assert profiles["towel"].solver == "SolverMuJoCo + SolverVBD"


def test_rss_interpreter_override_is_probed_without_touching_pipeline(tmp_path):
    executable = tmp_path / "rss-python"
    executable.write_text(f"#!{sys.executable}\nprint({json.dumps(metadata())!r})\n")
    executable.chmod(0o755)
    configs = config(tmp_path, python="/unused/pipeline/python")
    path = next(iter(configs.values()))
    path.write_text(path.read_text() + f"  robosimstudio: {json.dumps(str(executable))}\n")
    result = inspect_runtime(tmp_path, configs)
    assert result.status == "environment_detected"
    assert result.python == str(executable)
