"""RSS CLI interpreter routing without importing or stepping physics."""
import sys
from pathlib import Path

import pytest
import yaml


class Reexecuted(Exception):
    pass


def test_rss_cli_keeps_distinct_virtualenv_even_with_same_base_python(tmp_path, monkeypatch):
    import run_robosimstudio as cli
    import agent.rss_runtime as runtime
    python = tmp_path / "rss-env" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    assert python.resolve() == Path(sys.executable).resolve()
    config = tmp_path / "rss.yaml"
    config.write_text(yaml.safe_dump({"environments": {"pipeline": sys.executable,
                                                       "robosimstudio": str(python)}}))
    argv = ["rigid", "--config", str(config), "--no-render"]
    captured = []

    def reexec(executable, command):
        captured.append((executable, command))
        raise Reexecuted

    monkeypatch.setattr(cli.os, "execv", reexec)
    monkeypatch.setattr(runtime, "default_physics_device", lambda *_: pytest.fail("must re-exec before physics setup"))
    with pytest.raises(Reexecuted):
        cli.main(argv)
    assert captured == [(str(python), [str(python), str(Path(cli.__file__).resolve()), *argv])]


def test_rss_cli_does_not_reexec_when_selected_interpreter_is_running(tmp_path, monkeypatch):
    import run_robosimstudio as cli
    import agent.rss_runtime as runtime
    config = tmp_path / "rss.yaml"
    config.write_text(yaml.safe_dump({"environments": {"pipeline": "/unused/pipeline/python",
                                                       "robosimstudio": sys.executable}}))
    monkeypatch.setattr(cli.os, "execv", lambda *_: pytest.fail("already in the RSS interpreter"))

    def reached_configuration(*_):
        raise Reexecuted

    monkeypatch.setattr(runtime, "default_physics_device", reached_configuration)
    with pytest.raises(Reexecuted):
        cli.main(["rigid", "--config", str(config)])
