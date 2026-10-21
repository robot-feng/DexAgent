"""Interpret native reports without changing their acceptance semantics."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Callable

from agent.run_reports import status_for_checks

from .models import Artifact, Check, Issue, Report, Validation

MAX_REPORT_BYTES = 32 * 1024 * 1024
KNOWN_REPORT_SCHEMAS = {'dexagent.step_report.v1', 'dexagent.stage4_run_report.v1'}
STAGE_TITLES = {
    'preflight': '输入与环境检查', 'stage1': '演示理解', 'stage2': '场景重建',
    'stage3': '机器人轨迹', 'stage4': '数据生成', 'backend': '目标后端验收',
    'reference': '参考轨迹提取', 'execution': '物理执行',
    'training': '策略训练', 'evaluation': '策略闭环评估',
}

# Optional adapters emit the same native report contract; absent stages are not required.
INDEXED_STAGE_DIRS = ("stage1", "stage2", "reference", "stage3", "execution", "stage4", "training", "evaluation")
SENSITIVE_PROVENANCE_KEY_PARTS = (
    'apikey', 'token', 'password', 'passwd', 'secret', 'authorization',
    'cookie', 'credential', 'privatekey',
)


def load_object(path: Path) -> tuple[dict, bytes]:
    if path.stat().st_size > MAX_REPORT_BYTES:
        raise ValueError('report exceeds the 32 MiB preview limit')
    payload = path.read_bytes()
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError('expected a JSON object')
    return value, payload


def read_object(path: Path, issues: list[Issue]) -> dict | None:
    if not path.is_file():
        return None
    try:
        return load_object(path)[0]
    except (OSError, ValueError) as exc:
        issues.append(Issue(path=str(path), message=f'{type(exc).__name__}: {exc}'))
        return None


def report_validation(data: dict) -> Validation:
    if data.get('schema') not in KNOWN_REPORT_SCHEMAS:
        return 'incomplete'
    status = data.get('status')
    if status in {'running', 'pending'}:
        return 'not_evaluated'
    if status in {'failed', 'blocked', 'cancelled', 'interrupted'}:
        return 'failed' if status == 'failed' else 'incomplete'
    if data.get('schema') == 'dexagent.step_report.v1':
        checks = data.get('checks')
        if not isinstance(checks, list) or any(not isinstance(c, dict) for c in checks):
            return 'incomplete'
        measured = status_for_checks(checks)
        if measured == 'failed':
            return 'failed' if status == 'failed' else 'incomplete'
        if status not in {'passed', 'warning', 'reused'}:
            return 'incomplete'
        return 'warning' if measured == 'warning' or status in {'warning', 'reused'} else 'passed'
    steps, requested = data.get('steps'), data.get('steps_requested')
    if not isinstance(steps, dict) or not isinstance(requested, list) or not requested:
        return 'incomplete'
    values = []
    for name in requested:
        step = steps.get(name) if isinstance(name, str) else None
        if not isinstance(step, dict) or step.get('schema') != 'dexagent.step_report.v1':
            return 'incomplete'
        values.append(report_validation(step))
    if any(value not in {'passed', 'warning'} for value in values):
        return 'incomplete'
    return 'warning' if 'warning' in values or status == 'passed_with_warnings' else 'passed' if status == 'passed' else 'incomplete'


def _redact_provenance(value):
    """Keep native report provenance useful without returning credential values."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            normalized = ''.join(char for char in str(key).casefold() if char.isalnum())
            sensitive = any(part in normalized for part in SENSITIVE_PROVENANCE_KEY_PARTS)
            result[key] = '[REDACTED]' if sensitive else _redact_provenance(item)
        return result
    if isinstance(value, list):
        return [_redact_provenance(item) for item in value]
    return value


def parse_report(path: Path, register: Callable[[Path], Artifact]) -> Report:
    data, payload = load_object(path)
    metrics = data.get('metrics')
    metrics = metrics if isinstance(metrics, dict) else {}
    scope = data.get('scope', metrics.get('scope'))
    task_evaluated = data.get('task_success_evaluated', metrics.get('task_success_evaluated'))
    provenance_issues = []
    metadata = {}
    for key in ('inputs', 'model_identity'):
        value = data.get(key)
        metadata[key] = _redact_provenance(value) if isinstance(value, dict) else {}
        if value is not None and not isinstance(value, dict):
            provenance_issues.append(f'{key}: expected an object; inspect the original report')
    declared = data.get('artifacts')
    artifacts = [value for value in declared if isinstance(value, str)] if isinstance(declared, list) else []
    if declared is not None and (not isinstance(declared, list) or len(artifacts) != len(declared)):
        provenance_issues.append('artifacts: expected a list of strings; unsupported entries were not interpreted')
    config_sha = data.get('config_sha256')
    if config_sha is not None and not isinstance(config_sha, str):
        provenance_issues.append('config_sha256: expected a string; inspect the original report')
    checks = []
    for item in data.get('checks', []) if isinstance(data.get('checks'), list) else []:
        if not isinstance(item, dict):
            continue
        checks.append(Check(id=str(item.get('id', 'unnamed')), status=str(item.get('status', 'unknown')),
                            required=item.get('required', True) is not False,
                            observed=item.get('observed'), threshold=item.get('threshold'),
                            evidence=item.get('evidence')))
    return Report(artifact=register(path), schema_id=data.get('schema'),
                  source_status=data.get('status'), validation=report_validation(data), checks=checks,
                  evaluation_scope=scope if isinstance(scope, str) else None,
                  task_success_evaluated=task_evaluated if isinstance(task_evaluated, bool) else None,
                  elapsed_s=data.get('elapsed_s'), failure=data.get('failure'),
                  next_action=data.get('next_action'), attempt_id=data.get('attempt_id'),
                  sha256=hashlib.sha256(payload).hexdigest(), **metadata,
                  config_sha256=config_sha if isinstance(config_sha, str) else None,
                  declared_artifacts=artifacts, provenance_issues=provenance_issues)


def merge_validation(states: list[Validation], *, has_errors: bool = False) -> Validation:
    if 'failed' in states:
        return 'failed'
    if has_errors or 'incomplete' in states:
        return 'incomplete'
    if 'warning' in states:
        return 'warning'
    if states and all(s == 'passed' for s in states):
        return 'passed'
    return 'not_evaluated'
