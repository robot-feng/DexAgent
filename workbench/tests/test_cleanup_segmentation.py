"""Native SAM3 LiteText runtime preflight must fail closed."""

from workbench.cleanup_segmentation import runtime


def test_runtime_reports_missing_project_config_as_unavailable(tmp_path):
    result = runtime(tmp_path)
    assert result["ready"] is False
    assert "不可用" in result["detail"]
