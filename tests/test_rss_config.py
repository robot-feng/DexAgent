"""Interpreter inheritance at the YAML merge boundary; no physics imports."""

import pytest
import yaml

from agent import config
from agent.rss_runtime import rss_python


@pytest.fixture
def defaults(tmp_path, monkeypatch):
    path = tmp_path / "defaults.yaml"
    path.write_text(yaml.safe_dump({
        "environments": {"pipeline": "/default/pipeline/python",
                         "robosimstudio": "/default/rss/python"},
        "stage1": {"max_rounds": 20},
    }))
    monkeypatch.setattr(config, "DEFAULT_YAML", path)
    return path


@pytest.mark.parametrize("override, expected", [
    ({"stage1": {"max_rounds": 3}}, "/default/rss/python"),
    ({"environments": {"pipeline": "/legacy/python"}}, "/legacy/python"),
    ({"environments": {"pipeline": "/legacy/python", "robosimstudio": "/chosen/rss"}},
     "/chosen/rss"),
    ({"environments": {"robosimstudio": "/chosen/rss"}}, "/chosen/rss"),
    ({"environments": {"pipeline": None}}, "/default/rss/python"),
    ({"environments": {"pipeline": ""}}, "/default/rss/python"),
    ({"environments": {"pipeline": "/legacy/python", "robosimstudio": None}},
     "/default/rss/python"),
    ({"environments": {"pipeline": "/legacy/python", "robosimstudio": ""}},
     "/default/rss/python"),
])
def test_rss_interpreter_inheritance(defaults, tmp_path, override, expected):
    path = tmp_path / "run.yaml"
    path.write_text(yaml.safe_dump(override))
    loaded = config.load_config(path)
    assert rss_python(loaded) == expected
    assert loaded["environments"]["pipeline"] == (
        override.get("environments", {}).get("pipeline") or "/default/pipeline/python")
    assert loaded["stage1"]["max_rounds"] == override.get("stage1", {}).get("max_rounds", 20)

    # Workbench freezes merged configs before execution/retry. Reloading that
    # snapshot must preserve either explicit RSS selection or legacy fallback.
    frozen = tmp_path / "frozen.yaml"
    frozen.write_text(yaml.safe_dump(loaded))
    assert rss_python(config.load_config(frozen)) == expected


def test_default_config_keeps_dedicated_rss_interpreter(defaults):
    assert rss_python(config.load_config()) == "/default/rss/python"
