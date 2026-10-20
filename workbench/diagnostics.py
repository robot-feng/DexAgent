"""Read native planning trials without granting episode or release eligibility."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path

from .index import identity
from .models import Issue, PlanningStep, PlanningTrial, PlanningTrialDetail, PlanningTrials
from .reports import load_object

MAX_DIRECTORIES = 10_000
MAX_REPORTS = 200


def local_report(directory: Path, path: Path) -> Path:
    """Only reports in this run's Stage 3; reject even in-root symlink aliases."""
    root = directory / 'stage3'
    if not path.is_relative_to(root) or path.name != 'execution_report.json':
        raise ValueError('not a Stage 3 planning report for this run')
    for part in (path, *path.parents):
        if part == directory:
            break
        if part.is_symlink():
            raise ValueError('planning report must not traverse symbolic links')
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('planning report is outside this run')
    return path


def parse_trial(path: Path, register) -> PlanningTrialDetail:
    data, payload = load_object(path)
    # Starlette's JSON response cannot serialize NaN/Infinity in native measurements.
    json.dumps(data, allow_nan=False)
    steps = data.get('subgoals')
    expected = data.get('n_subgoals')
    if (type(data.get('ok')) is not bool or type(data.get('dry_run')) is not bool
            or type(expected) is not int or not 1 <= expected <= 1000
            or not isinstance(steps, list) or len(steps) > 1000):
        raise ValueError('unrecognized native execution report structure')
    parsed = []
    for step in steps:
        if (not isinstance(step, dict) or type(step.get('index')) is not int
                or step['index'] < 1 or not isinstance(step.get('motion'), str)
                or type(step.get('ok')) is not bool
                or not isinstance(step.get('failed', []), list)
                or any(not isinstance(v, str) for v in step.get('failed', []))
                or not isinstance(step.get('measured', {}), dict)):
            raise ValueError('invalid native subgoal record')
        frames = step.get('frames')
        if frames is not None and (not isinstance(frames, list) or len(frames) != 2
                                   or any(type(n) is not int or n < 0 for n in frames)
                                   or frames[0] > frames[1]):
            raise ValueError('invalid subgoal frame range')
        parsed.append(PlanningStep(
            index=step['index'], motion=step['motion'], ok=step['ok'],
            side=step.get('side') if isinstance(step.get('side'), str) else None,
            object=step.get('object') if isinstance(step.get('object'), str) else None,
            failed=step.get('failed', []), measured=step.get('measured', {}), frames=frames,
            error=step.get('error') if isinstance(step.get('error'), str) else None))
    # Surface contradictions; never convert a partial/dry run into a task certificate.
    failed = list(dict.fromkeys(check for step in parsed for check in step.failed))
    passed = sum(step.ok and not step.failed and not step.error for step in parsed)
    complete = ([step.index for step in parsed] == list(range(1, expected + 1))
                and passed == expected)
    outcome = ('failed' if data['ok'] is False else
               'passed' if complete else 'incomplete')
    duration = data.get('duration_s')
    duration = (duration if type(duration) in (int, float)
                and math.isfinite(duration) and duration >= 0 else None)
    frames = data.get('n_frames')
    return PlanningTrialDetail(
        report=register(path), sha256=hashlib.sha256(payload).hexdigest(),
        outcome=outcome, dry_run=data['dry_run'], expected_subgoals=expected,
        recorded_subgoals=len(parsed), passed_subgoals=passed, failed_checks=failed,
        duration_s=duration, n_frames=frames if type(frames) is int and frames >= 0 else None,
        subgoals=parsed)


def planning_trials(index, run_id: str) -> PlanningTrials:
    directory, _ = index.run_path(run_id)
    root = directory / 'stage3'
    issues, paths = [], []
    truncated = False
    if root.is_symlink():
        return PlanningTrials(items=[], issues=[Issue(path=str(root), message='symbolic Stage 3 directory rejected')])
    # .candidates is an intentional native workspace. Other hidden trees stay private.
    for count, (parent, dirs, files) in enumerate(os.walk(root, followlinks=False)):
        dirs[:] = sorted(d for d in dirs if (not d.startswith('.') or d == '.candidates')
                         and d not in {'__pycache__', 'external'}
                         and not (Path(parent) / d).is_symlink())
        if count >= MAX_DIRECTORIES:
            truncated = True
            break
        if 'execution_report.json' in files:
            path = Path(parent) / 'execution_report.json'
            if len(paths) >= MAX_REPORTS:
                truncated = True
                break
            paths.append(path)
    items = []
    for path in paths:
        try:
            detail = parse_trial(local_report(directory, path), lambda p: index.artifact(p, run_id))
            items.append(PlanningTrial(**detail.model_dump(exclude={'subgoals'})))
        except (OSError, ValueError) as exc:
            issues.append(Issue(path=index.label(path), message=str(exc)))
    items.sort(key=lambda trial: (trial.report.modified, trial.report.path), reverse=True)
    return PlanningTrials(items=items, issues=issues, truncated=truncated)


def planning_trial(index, run_id: str, report_id: str) -> PlanningTrialDetail:
    directory, _ = index.run_path(run_id)
    path = local_report(directory, index.artifact_path(report_id))
    if identity(str(path)) != report_id:
        raise ValueError('planning report path changed after registration')
    return parse_trial(path, lambda p: index.artifact(p, run_id))
