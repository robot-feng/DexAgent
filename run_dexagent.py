"""DexAgent end to end: a video and a task prompt in, randomized robot videos out, with every stage's progress on the
terminal (the round out of its budget, the tools called and what they returned, what the model is thinking, the
verifiers' verdicts, the tools it created) and a closing table.

    python run_dexagent.py dexagent.yaml                      # fresh Stage 1 -> 4 evaluation
    python run_dexagent.py dexagent.yaml --profile smoke      # reduced end-to-end check, examples may be reused
    python run_dexagent.py dexagent.yaml --profile dataset_release
    python run_dexagent.py dexagent.yaml --from stage3        # resume from a stage (earlier deliverables must exist)
    python run_dexagent.py dexagent.yaml --to stage3          # stop after a stage
    python run_dexagent.py dexagent.yaml --from stage2 --to stage3 --resume-stage3-attempt runs/demo/stage3/.candidates/<id>
    python run_dexagent.py dexagent.yaml --name demo --video v.mp4 --prompt "close the box"
    python run_dexagent.py dexagent.yaml --confirm-stages     # pause between stages

Every stage writes a machine-readable report and prints its verifier status and artifact paths. Required checks gate the
next step; stage transitions continue automatically unless --confirm-stages is given. Partial runs and user stops are
recorded as incomplete, never as a full pipeline pass. A stage that asks to re-open an earlier one (cross-stage repair,
below) is still confirmed first unless --yes is given.

Each stage also runs on its own (stage1_semantic_understanding/run_stage1.py, ...). Progress of the running stage is in
runs/<name>/<stage>/progress.json; the run's summary in runs/<name>/run_summary.json.

Cross-stage repair: a stage that ends with report_blocked(upstream='stage1' | 'stage2', evidence) re-opens that earlier stage
with the reason in its first message, and the stages after it run again (repair.max_loops_per_stage re-openings per stage,
default 1). Every loop is recorded in run_summary.json and, when the run fails, in runs/<name>/failure_report.json.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

from agent import repair  # noqa: E402
from agent.config import apply_profile, load_config, run_dir, semantics_file  # noqa: E402
from agent.console import STAGES, Console, summary_table  # noqa: E402
from agent.preflight import preflight  # noqa: E402
from agent.run_reports import (atomic_json, finish_report, make_report, print_report,
                              status_for_checks, verifier_checks)  # noqa: E402

ORDER = ["stage1", "stage2", "stage3", "stage4"]


def is_interruption(exc: BaseException) -> bool:
    return isinstance(exc, KeyboardInterrupt) or (isinstance(exc, SystemExit) and exc.code == 130)


def run_agent_stage(stage: str, cfg_path: str, extra: list[str], cfg: dict) -> dict:
    mod = {"stage1": "stage1_semantic_understanding.run_stage1", "stage2": "stage2_scene_reconstruction.run_stage2",
           "stage3": "stage3_trajectory_generation.run_stage3"}[stage]
    ws = run_dir(cfg, stage)
    attempt_id = str(uuid.uuid4())
    attempt_dir = ws / "reports" / "attempts" / attempt_id
    out = {"stage": stage, "finished": False, "workspace": str(ws), "attempt_id": attempt_id}
    try:
        attempt_dir.mkdir(parents=True, exist_ok=False)
        rj = ws / "run.json"
        if rj.exists():
            rj.replace(attempt_dir / "prior_run.json")
        import importlib
        m = importlib.import_module(mod)
        # The stage's main prints the console itself. Its current run.json is captured after it returns.
        rc = m.main([cfg_path] + extra)
        try:
            rec = json.loads(rj.read_text()) if rj.is_file() else None
            if not isinstance(rec, dict):
                rec = None
        except (OSError, ValueError):
            rec = None
        out.update({"finished": rc == 0, "run_record": rec})
        if rc == 130:
            out["interrupted"] = True
        if rec is None:
            out["finished"] = False
            out["failure"] = "stage did not produce a readable run.json for this attempt"
        else:
            out["reused_example"] = rec.get("reused_example")
            for k in ("failure", "upstream", "methods", "agent_search", "stopped", "stop_reason", "search_checkpoint", "spent_usd",
                      "run_spent_usd", "budget_usd", "tools_used"):
                if rec.get(k):
                    out[k] = rec[k]
            if rc != 0 and not out.get("failure"):
                out["failure"] = f"{stage} runner exited with code {rc}"
        prog = ws / "progress.json"
        if prog.exists():
            try:
                p = json.loads(prog.read_text())
                out.update({"rounds": p.get("round"), "tool_calls": p.get("tool_calls"), "new_tools": p.get("new_tools"),
                            "elapsed_s": p.get("elapsed_s")})
            except (OSError, ValueError):
                pass
    except BaseException as exc:
        # Stage execution failures, including cooperative process interruption, are reported by the pipeline.
        out["interrupted"] = is_interruption(exc)
        out["failure"] = f"{type(exc).__name__}: {exc}" or type(exc).__name__
        out["run_record"] = None
        print(f"  {out['failure']}", flush=True)
    return out


def apply_stage4_settings(task: Path, s4: dict) -> dict:
    """The `stage4` block of dexagent.yaml (samples, seed, views, render quality) written into the task's stage4.json."""
    c = json.loads(task.read_text())
    for k in ("num_samples", "seed"):
        if s4.get(k) is not None:
            c[k] = s4[k]
    if s4.get("views") is not None:
        views = (c.get("cameras") or {}).get("views") or {}
        c.setdefault("cameras", {})["views"] = {k: v for k, v in views.items() if k in set(s4["views"])}
    for k, v in (s4.get("render") or {}).items():
        c.setdefault("render", {})[k] = v
    if s4.get("segmentation_backend"):
        c.setdefault("inpaint", {})["segmentation_backend"] = s4["segmentation_backend"]
    if s4.get("inpaint_backend"):
        c.setdefault("inpaint", {})["backend"] = s4["inpaint_backend"]
    if s4.get("lama_checkpoint"):
        c.setdefault("inpaint", {})["lama_model"] = s4["lama_checkpoint"]
    c.setdefault("render", {}).setdefault("parallel", 1)
    c["evaluation_profile"] = s4.get("evaluation_profile", "pipeline_eval")
    if c["evaluation_profile"] == "smoke":
        smoke = s4.get("smoke_stage4") or {}
        for k in ("num_samples", "seed"):
            if smoke.get(k) is not None:
                c[k] = smoke[k]
        if smoke.get("views") is not None:
            views = (c.get("cameras") or {}).get("views") or {}
            c.setdefault("cameras", {})["views"] = {k: v for k, v in views.items() if k in set(smoke["views"])}
        for k, v in (smoke.get("render") or {}).items():
            c.setdefault("render", {})[k] = v
        if smoke.get("max_frames") is not None:
            c.setdefault("inpaint", {})["max_frames"] = smoke["max_frames"]
    task.write_text(json.dumps(c, indent=1))
    return c


def terminate_process_group(proc: subprocess.Popen, grace_s: float = 15.0) -> None:
    """Stop a stage process and its children after the pipeline itself is interrupted."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=grace_s)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()


def run_stage4(cfg: dict) -> dict:
    """Stage 4 is not agentic: a fixed five-step pipeline in its own environment, driven by the task folder Stage 3 wrote
    (runs/<name>/stage3/stage4_task/, the copy of the accepted delivery's; its outputs go to stage4_task/outputs/)."""
    attempt_id = str(uuid.uuid4())
    ws3 = run_dir(cfg, "stage3"); ws4 = run_dir(cfg, "stage4")
    try:
        from agent.artifacts import accepted_delivery
        accepted = accepted_delivery(ws3)
    except (OSError, ValueError, KeyError) as exc:
        return {"stage": "stage4", "finished": False, "workspace": str(ws4), "attempt_id": attempt_id,
                "failure": f"no intact accepted Stage 3 delivery: {exc}"}
    # Stage 4 writes settings and its outputs (stage4_task/outputs/) only to the copy of the sealed input at the top of
    # the Stage 3 folder; a copy that is missing or no longer the accepted one is refreshed (the old one is kept aside)
    from agent.artifacts import expose_accepted
    expose_accepted(ws3, accepted)                 # a fresh copy for this run; an earlier one (and its outputs) is kept aside
    task_dir = ws3 / "stage4_task"
    task = task_dir / "stage4.json"
    con = Console("stage4", str(cfg["run"]["name"]), ws4, max_rounds=0)
    con.banner(note=f"no model rounds: randomized episodes from {task_dir}")
    if not task.exists():
        if (accepted / "twin_episode.json").exists():   # a reused Isaac Sim twin episode: no rigid runtime recording to randomize
            msg = ("Stage 3 published a recorded twin episode (twin_episode.json), not a rigid runtime recording of the "
                   "setup: Stage 4 does not randomize it")
            con._out(f"  {msg}", "yellow")
            return {"stage": "stage4", "finished": False, "skipped": True, "workspace": str(ws4),
                    "attempt_id": attempt_id, "failure": msg}
        con._out(f"  Stage 3 left no stage4_task/stage4.json in {ws3}", "red")
        return {"stage": "stage4", "finished": False, "workspace": str(ws4), "attempt_id": attempt_id,
                "failure": f"Stage 3 left no stage4_task/stage4.json in {ws3}"}
    s4_settings = dict(cfg.get("stage4") or {})
    s4_settings["evaluation_profile"] = (cfg.get("evaluation") or {}).get("profile", "pipeline_eval")
    s4_settings["smoke_stage4"] = (cfg.get("evaluation") or {}).get("smoke_stage4") or {}
    s4_settings["segmentation_backend"] = (cfg.get("models") or {}).get("segmentation_backend", "sam3-litetext-s0")
    s4_settings["lama_checkpoint"] = (cfg.get("models") or {}).get("lama_checkpoint")
    apply_stage4_settings(task, s4_settings)
    python = (cfg.get("environments") or {}).get("stage4") or sys.executable
    cmd = [python, str(REPO / "stage4_robot_data_generation" / "run_stage4.py"), str(task)]
    t0 = time.time()
    con._out("  ▶ " + " ".join(cmd), "bold")
    env = dict(os.environ); env.setdefault("MUJOCO_GL", "egl"); env.setdefault("PYTHONUNBUFFERED", "1")   # progress lines as they happen
    env["DEXAGENT_STAGE4_INVOCATION_ID"] = attempt_id
    models = cfg.get("models") or {}
    if models.get("void_dir"):                     # the VOID checkout the plate step runs (default: third_party/void-model)
        env["VOID_DIR"] = str(models["void_dir"])
    if models.get("hf_home"):                      # where SAM 3, VOID and CogVideoX weights are cached (as for the tools)
        env["HF_HOME"] = str(models["hf_home"])
    if models.get("blender_executable"):
        env["DEXAGENT_BLENDER"] = str(models["blender_executable"])
    if models.get("sam3_litetext_dir"):
        env["DEXAGENT_SAM3_LITETEXT_PATH"] = str(models["sam3_litetext_dir"])
    if models.get("lama_checkpoint"):
        env["DEXAGENT_LAMA_MODEL"] = str(models["lama_checkpoint"])
    env["DEXAGENT_SEGMENTATION_BACKEND"] = str((json.loads(task.read_text()).get("inpaint") or {}).get(
        "segmentation_backend", "sam3-litetext-s0"))
    if (cfg.get("environments") or {}).get("isaac"):   # deformables are replayed in Isaac Sim
        env.setdefault("DEXAGENT_ISAAC_PYTHON", str(cfg["environments"]["isaac"]))
    proc = subprocess.Popen(cmd, cwd=str(REPO / "stage4_robot_data_generation"), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, env=env, start_new_session=True)
    try:
        for line in proc.stdout:
            line = line.rstrip()
            if line.startswith("[stage4]") or line.startswith("[render]") or "finished" in line or "error" in line.lower():
                con._out("    " + line[:200], "dim")
        rc = proc.wait()
    except BaseException as exc:
        terminate_process_group(proc)
        interrupted = is_interruption(exc)
        failure = f"{type(exc).__name__}: {exc}" or type(exc).__name__
        con.done({"finished": False, "rounds": "-"})
        return {"stage": "stage4", "finished": False, "interrupted": interrupted,
                "workspace": str(ws4), "attempt_id": attempt_id,
                "elapsed_s": round(time.time() - t0, 1), "failure": failure}
    finally:
        if proc.stdout is not None:
            proc.stdout.close()
    stage4_report = task_dir / "outputs" / "run_report.json"
    run_record = None
    if stage4_report.is_file():
        try:
            candidate = json.loads(stage4_report.read_text())
            if isinstance(candidate, dict) and candidate.get("invocation_id") == attempt_id:
                run_record = candidate
        except (OSError, ValueError):
            run_record = None
    out = {"stage": "stage4", "finished": rc == 0 and run_record is not None, "workspace": str(ws4),
           "attempt_id": attempt_id, "stage4_run_record": run_record,
           "elapsed_s": round(time.time() - t0, 1), "rounds": "-",
           "steps_completed": [name for name, rep in (run_record.get("steps") or {}).items()
                               if isinstance(rep, dict) and rep.get("status") in ("passed", "warning")]
           if run_record else [],
           "stage4_report": str(stage4_report),
           "stage4_status": run_record.get("status") if run_record else None,
           "release_ready": run_record.get("release_ready") if run_record else None,
           "interrupted": rc == 130 or rc in (-signal.SIGINT, -signal.SIGTERM)}
    if rc != 0:
        out["failure"] = f"run_stage4.py exited with code {rc} (its log lines above; outputs in {task_dir / 'outputs'})"
    elif run_record is None:
        out["failure"] = (f"run_stage4.py did not write a current run report with invocation_id {attempt_id} "
                          f"(outputs in {task_dir / 'outputs'})")
    con.done({"finished": out["finished"], "rounds": "-"})
    return out


def stage_outputs(cfg: dict, stage: str) -> list[str]:
    """What a finished stage produced, with the files to open to check it."""
    ws = run_dir(cfg, stage)
    lines = []
    try:
        if stage == "stage1":
            sem = semantics_file(ws)
            if sem.exists():
                d = json.loads(sem.read_text())
                lines.append(f"subgoals ({len(d.get('subgoals') or [])}): " + "; ".join(
                    f"{g.get('index')}. {g.get('action') or g.get('goal') or g.get('motion')}" for g in (d.get("subgoals") or [])[:8])[:600])
                lines.append(f"scene semantics: {sem}")
        elif stage == "stage2":
            rec = ws / "scene_recon.json"
            if rec.exists():
                d = json.loads(rec.read_text())
                lines.append("objects: " + ", ".join(str(o.get("id")) for o in d.get("objects") or []))
                lines.append(f"scene: {ws / d.get('scene_xml', 'scene.xml')}")
            for name in ("frame0_compare.png", "scene_recon.json"):
                if (ws / name).exists():
                    lines.append(f"check: {ws / name}")
        elif stage == "stage3":
            for name in ("sim_video.mp4", "stage4_task/stage4.json", "trajectory_manifest.json"):
                if (ws / name).exists():
                    lines.append(f"{'check' if name.endswith('.mp4') else 'output'}: {ws / name}")
        elif stage == "stage4":
            outs = sorted((run_dir(cfg, "stage3") / "stage4_task" / "outputs").glob("*"))
            if outs:
                lines.append(f"outputs ({len(outs)}): {outs[0].parent}")
    except (OSError, ValueError) as exc:
        lines.append(f"(could not read the stage's outputs: {exc})")
    return lines


def effective_config_sha256(preflight_report: dict) -> str | None:
    """Read the effective config digest, with compatibility for older preflight input shapes."""
    if preflight_report.get("config_sha256"):
        return preflight_report["config_sha256"]
    inputs = preflight_report.get("inputs") or {}
    config = inputs.get("config") or {}
    source = inputs.get("source") or {}
    config_source = config.get("source") or {}
    source = source if isinstance(source, dict) else {}
    config_source = config_source if isinstance(config_source, dict) else {}
    config = config if isinstance(config, dict) else {}
    for value in (inputs.get("effective_sha256"), source.get("sha256"),
                  config.get("effective_sha256"), config_source.get("sha256"), config.get("sha256")):
        if value:
            return value
    return None


def latest_stage_results(results: list[dict]) -> dict[str, dict]:
    """Return each stage's final invocation result, including repair reruns."""
    latest = {}
    for result in results:
        latest[result.get("stage")] = result
    return latest


def report_stage(r: dict, cfg: dict):
    """The block printed when a stage ends: verdict, effort, tools used and built, money, what to check."""
    num, name = STAGES[r["stage"]]
    verdict = r.get("report_status", "passed" if r.get("finished") else "failed").upper()
    print(f"\n══ Stage {num} · {name}: {verdict} ══", flush=True)
    bits = []
    if r.get("reused_example"):
        bits.append(f"reused the verified example {r['reused_example']}")
    if r.get("rounds") not in (None, "-"):
        bits.append(f"{r['rounds']} rounds")
    if r.get("tool_calls"):
        bits.append(f"{r['tool_calls']} tool calls")
    if r.get("steps_completed") is not None:
        bits.append(f"{len(r['steps_completed'])}/5 Stage 4 steps")
    if r.get("elapsed_s"):
        bits.append(f"{float(r['elapsed_s']) / 60:.1f} min")
    if r.get("spent_usd") is not None:
        bits.append(f"${float(r['spent_usd']):.2f} (run: ${float(r.get('run_spent_usd') or 0):.2f} of ${float(r.get('budget_usd') or 0):g})")
    if bits:
        print("   " + " · ".join(bits))
    used = r.get("tools_used") or {}
    if used:
        top = sorted(used.items(), key=lambda kv: -kv[1])
        print("   tools used: " + ", ".join(f"{k}×{v}" for k, v in top[:12]) + (f", ... ({len(top)} distinct)" if len(top) > 12 else ""))
    if r.get("new_tools"):
        print("   tools built: " + ", ".join(f"{t.get('type', 'tool')} {t.get('name')}" for t in r["new_tools"]))
    if not r.get("finished") and r.get("failure"):
        print(f"   why: {str(r['failure'])[:400]}")
    if r.get("report_path"):
        print(f"   report: {r['report_path']}")
    for line in stage_outputs(cfg, r["stage"]) if r.get("finished") else []:
        print("   " + line)
    sys.stdout.flush()


def continue_command(a, nxt: str) -> str:
    """The command that continues this run from stage `nxt`, with every flag this run was given."""
    import shlex
    parts = ["python", "run_dexagent.py", a.config, "--from", nxt]
    parts += ["--profile", a.profile]
    if a.confirm_stages:
        parts.append("--confirm-stages")
    if a.stop != "stage4":
        parts += ["--to", a.stop]
    for flag, value in (("--name", a.name), ("--video", a.video), ("--prompt", a.prompt), ("--max-rounds", a.max_rounds),
                        ("--llm", a.llm if a.llm != "config" else None)):
        if value:
            parts += [flag, str(value)]
    return " ".join(shlex.quote(p) for p in parts)


def write_stage_report(cfg: dict, result: dict, profile: str, preflight_report: dict) -> dict:
    stage = result["stage"]
    if not result.get("attempt_id"):
        result["attempt_id"] = str(uuid.uuid4())
    workspace = Path(result.get("workspace") or run_dir(cfg, stage))
    workspace.mkdir(parents=True, exist_ok=True)
    stage4_ref = result.get("stage4_report")
    config_sha256 = effective_config_sha256(preflight_report)
    if stage == "stage4":
        source = result.get("stage4_run_record")
        source_error = None
        if not isinstance(source, dict):
            source = {}
            source_error = "Stage 4 did not provide a current run report snapshot"
        elif not result.get("attempt_id") or source.get("invocation_id") != result.get("attempt_id"):
            source_error = "Stage 4 run report invocation_id does not match this attempt"
        steps = source.get("steps") or {}
        if not isinstance(steps, dict):
            steps = {}
        checks = []
        for name in ("randomize", "plate", "export", "render", "composite"):
            step = steps.get(name)
            step = step if isinstance(step, dict) else {}
            checks.append({"id": name, "required": True, "status": step.get("status", "missing"),
                           "evidence": step.get("failure"), "report": step.get("report_path")})
        if source_error:
            checks.append({"id": "stage4_run_report", "required": True, "status": "failed", "evidence": source_error})
        if source.get("status") not in {"passed", "passed_with_warnings"}:
            checks.append({"id": "stage4_complete_run", "required": True, "status": "failed",
                           "observed": source.get("status"),
                           "evidence": "top-level pipeline requires all five Stage 4 steps to complete"})
        status = status_for_checks(checks)
        if source.get("status") not in {"passed", "passed_with_warnings"}:
            status = "failed"
        elif status == "passed" and source.get("status") == "passed_with_warnings":
            status = "warning"
        if not result.get("finished"):
            checks.append({"id": "stage_execution", "required": True, "status": "failed",
                           "evidence": result.get("failure") or "Stage 4 execution did not finish"})
            status = "failed"
        report = make_report(run=str(cfg["run"]["name"]), profile=profile, stage=stage,
                             status=status, checks=checks,
                             inputs={"preflight_report": str(Path(cfg["run"]["output_dir"]) / cfg["run"]["name"] / "reports" / "preflight.json")},
                             config_sha256=config_sha256,
                             model_identity=preflight_report.get("model_identity"),
                             artifacts=[stage4_ref] if stage4_ref else [],
                             metrics={"steps": source.get("steps"), "release_ready": source.get("release_ready")},
                             elapsed_s=result.get("elapsed_s"), failure=result.get("failure"))
    else:
        run_record = result.get("run_record")
        current_run_error = None
        if not isinstance(run_record, dict):
            run_record = {}
            current_run_error = "stage did not provide a readable run.json snapshot for this attempt"
        reused_example = result.get("reused_example") or run_record.get("reused_example")
        if reused_example and profile == "smoke" and result.get("finished") and not current_run_error:
            status = "reused"
            checks = [{"id": "verified_example_reuse", "required": False, "status": "skipped",
                       "observed": reused_example,
                       "evidence": "no fresh model execution or current-run verifier pass"}]
        else:
            checks = verifier_checks(run_record.get("verdict"))
            if current_run_error:
                checks.append({"id": "current_run_record", "required": True, "status": "failed",
                               "evidence": current_run_error})
            if reused_example:
                checks.append({"id": "example_reuse_allowed", "required": True, "status": "failed",
                               "observed": reused_example,
                               "evidence": "example reuse is permitted only for the smoke profile"})
            if not result.get("finished"):
                checks.append({"id": "stage_execution", "required": True, "status": "failed",
                               "evidence": result.get("failure") or "stage execution did not finish"})
            status = status_for_checks(checks)
        artifacts = stage_outputs(cfg, stage)
        report = make_report(run=str(cfg["run"]["name"]), profile=profile, stage=stage,
                             status=status, checks=checks,
                             inputs={"video": preflight_report.get("inputs", {}).get("video"),
                                     "config_sha256": config_sha256},
                             config_sha256=config_sha256,
                             model_identity=preflight_report.get("model_identity"),
                             artifacts=artifacts, metrics={k: result.get(k) for k in ("rounds", "tool_calls", "elapsed_s", "spent_usd")},
                             elapsed_s=result.get("elapsed_s"), failure=result.get("failure"),
                             next_action=result.get("failure"))
    if result.get("attempt_id"):
        report["attempt_id"] = result["attempt_id"]
    report_path = workspace / "stage_report.json"
    atomic_json(report_path, report)
    result["report_path"] = str(report_path)
    result["report_status"] = report["status"]
    if report["status"] == "failed" and result.get("finished"):
        result["finished"] = False
        result["failure"] = result.get("failure") or "required stage report checks failed"
    return report


def start_stage_report(cfg: dict, stage: str, profile: str, preflight_report: dict) -> Path:
    workspace = run_dir(cfg, stage)
    report_path = workspace / "stage_report.json"
    report = make_report(run=str(cfg["run"]["name"]), profile=profile, stage=stage,
                         status="running", checks=[],
                         inputs={"video": preflight_report.get("inputs", {}).get("video")},
                         config_sha256=effective_config_sha256(preflight_report),
                         model_identity=preflight_report.get("model_identity"))
    atomic_json(report_path, report)
    return report_path


def write_unstarted_stage_report(cfg: dict, stage: str, profile: str, preflight_report: dict,
                                 status: str, reason: str) -> str:
    workspace = run_dir(cfg, stage)
    report_path = workspace / "stage_report.json"
    report = make_report(run=str(cfg["run"]["name"]), profile=profile, stage=stage,
                         status=status, checks=[{"id": "stage_execution", "required": True,
                                                 "status": status, "evidence": reason}],
                         config_sha256=effective_config_sha256(preflight_report),
                         model_identity=preflight_report.get("model_identity"),
                         failure=reason if status == "blocked" else None,
                         next_action=reason)
    atomic_json(report_path, report)
    return str(report_path)


def ask(question: str, auto: bool) -> bool:
    """Yes when --yes was given, else the user's answer on the terminal; no answer possible (no TTY) is no."""
    if auto:
        return True
    if not sys.stdin.isatty():
        print(f"{question} -- no terminal to answer on: stopping here (add --yes to run every stage without asking)", flush=True)
        return False
    while True:
        try:
            ans = input(f"{question} [y/n] ").strip().lower()
        except EOFError:
            return False
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no", "q", "quit", "stop"):
            return False


def apply_cli_runtime_options(cfg: dict, args) -> dict:
    """Add CLI-selected inputs and execution controls before preflight hashes the effective config."""
    if args.name:
        cfg["run"]["name"] = args.name
    if args.video:
        cfg["inputs"]["video"] = str(Path(args.video).resolve())
    if args.prompt is not None:
        cfg["inputs"]["task"] = args.prompt
    if args.max_rounds:
        for stage in ORDER[:3]:
            cfg.setdefault(stage, {})["max_rounds"] = args.max_rounds
    cfg["runtime_options"] = {
        "llm": args.llm,
        "resume_stage3_attempt": str(args.resume_stage3_attempt) if args.resume_stage3_attempt is not None else None,
    }
    return cfg


def _main_impl(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="DexAgent end to end")
    ap.add_argument("--backend-eval", choices=("rigid", "towel", "sharpa", "grasp", "reference", "simfoundry"),
                    help="evaluate the RoboSimStudio backend and its recorded artifacts")
    ap.add_argument("--reference-run", type=Path, help="DexAgent reference run for --backend-eval reference")
    ap.add_argument("--simfoundry-stage8", type=Path)
    ap.add_argument("--simfoundry-stage11", type=Path)
    ap.add_argument("--simfoundry-stage12", type=Path)
    ap.add_argument("--simfoundry-pose-info", type=Path)
    ap.add_argument("--simfoundry-collision", choices=("stage11", "convex_hull", "bounding_box"), default="stage11")
    ap.add_argument("config", nargs="?", default=os.environ.get("DEXAGENT_CONFIG", str(REPO / "dexagent.yaml")))
    ap.add_argument("--from", dest="start", default="stage1", choices=ORDER)
    ap.add_argument("--to", dest="stop", default="stage4", choices=ORDER)
    ap.add_argument("--name"); ap.add_argument("--video"); ap.add_argument("--prompt")
    ap.add_argument("--max-rounds", type=int, help="round budget for every agent stage (default: each stage's own)")
    ap.add_argument("--profile", choices=("smoke", "pipeline_eval", "dataset_release"),
                    help="evaluation profile (default: evaluation.profile in the config)")
    ap.add_argument("--resume-stage3-attempt", type=Path,
                    help="forward this unfinished candidate to Stage 3 --resume-attempt, including after upstream repair; all current input and acceptance checks still apply")
    ap.add_argument("--llm", default="config", help="config (the YAML's agent block) or mock:<turns.json> for a scripted dry run")
    ap.add_argument("--yes", "-y", action="store_true", help="run every stage without asking between stages")
    ap.add_argument("--confirm-stages", action="store_true", help="pause for confirmation between stages")
    a = ap.parse_args(argv)
    cfg = apply_profile(load_config(a.config), a.profile)
    if a.backend_eval:
        from run_robosimstudio import main as backend_main
        options = [a.backend_eval, "--config", str(cfg["_config_path"])]
        if a.reference_run is not None:
            options += ["--reference-run", str(a.reference_run)]
        if a.backend_eval == "simfoundry":
            for name in ("simfoundry_stage8", "simfoundry_stage11", "simfoundry_stage12", "simfoundry_pose_info"):
                if getattr(a, name) is not None:
                    options += ['--' + name.replace('_', '-'), str(getattr(a, name))]
            options += ["--simfoundry-collision", a.simfoundry_collision]
        return backend_main(options)
    a.profile = cfg["evaluation"]["profile"]
    cfg = apply_cli_runtime_options(cfg, a)
    name = str(cfg["run"]["name"])
    if ORDER.index(a.start) > ORDER.index(a.stop):
        ap.error(f"--from {a.start} comes after --to {a.stop}")
    stages = ORDER[ORDER.index(a.start): ORDER.index(a.stop) + 1]
    print(f"DexAgent run '{name}' [{a.profile}]: {' -> '.join(STAGES[s][1] for s in stages)}", flush=True)
    out_dir = Path(cfg["run"]["output_dir"]) / name
    report_dir = out_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    preflight_path = report_dir / "preflight.json"
    summary_path = out_dir / "run_summary.json"
    results, loops = [], []
    started_at = time.strftime("%Y-%m-%d %H:%M:%S")

    def persist_running_summary(active_stage=None):
        atomic_json(summary_path, {"run": name, "profile": a.profile, "status": "running", "ok": False,
                                   "scope_complete": False, "full_pipeline_complete": False,
                                   "requested_stages": stages, "preflight_report": str(preflight_path),
                                   "stages": results, "repair_loops": loops,
                                   "active_stage": active_stage, "started_at": started_at,
                                   "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")})

    persist_running_summary()
    preflight_running = make_report(run=name, profile=a.profile, stage="preflight", status="running")
    atomic_json(preflight_path, preflight_running)
    preflight_started = time.monotonic()
    try:
        preflight_report = preflight(cfg, stages, a.profile)
    except BaseException as exc:
        detail = f"{type(exc).__name__}: {exc}" or type(exc).__name__
        preflight_report = finish_report(preflight_running,
            checks=[{"id": "preflight_completed", "required": True, "status": "failed",
                     "evidence": detail}],
            failure=detail, next_action="fix preflight inputs and rerun")
        if is_interruption(exc):
            preflight_report["interrupted"] = True
    preflight_report["elapsed_s"] = round(time.monotonic() - preflight_started, 3)
    atomic_json(preflight_path, preflight_report)
    print_report(preflight_report, preflight_path)
    if preflight_report["status"] == "failed":
        blocked_stages = []
        for stage in stages:
            reason = "blocked by failed preflight checks"
            report_path = write_unstarted_stage_report(cfg, stage, a.profile, preflight_report,
                                                       "blocked", reason)
            blocked_stages.append({"stage": stage, "blocked": True, "finished": False,
                                   "workspace": str(run_dir(cfg, stage)), "report_path": report_path,
                                   "report_status": "blocked", "failure": reason})
        summary = {"run": name, "profile": a.profile, "status": "failed", "ok": False,
                   "full_pipeline_complete": False, "requested_stages": stages,
                   "preflight_report": str(preflight_path), "stages": blocked_stages,
                   "interrupted": bool(preflight_report.get("interrupted")),
                   "failure": preflight_report.get("failure") or "preflight required checks failed",
                   "finished_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        atomic_json(summary_path, summary)
        return 1
    persist_running_summary()
    ok = True
    max_loops = int((cfg.get("repair") or {}).get("max_loops_per_stage", 1))
    reopened = {}
    queue = list(stages)
    stopped_after, unanswered = None, False
    while queue:
        stage = queue.pop(0)
        start_stage_report(cfg, stage, a.profile, preflight_report)
        persist_running_summary({"stage": stage, "status": "running",
                                 "report_path": str(run_dir(cfg, stage) / "stage_report.json")})
        try:
            if stage == "stage4":
                r = run_stage4(cfg)
            else:
                extra = []
                if a.name:
                    extra += ["--name", a.name]
                if a.video and stage in ("stage1", "stage2", "stage3"):
                    extra += ["--video", a.video]
                if a.prompt is not None and stage == "stage1":
                    extra += ["--prompt", a.prompt]
                if a.max_rounds:
                    extra += ["--max-rounds", str(a.max_rounds)]
                extra += ["--profile", a.profile]
                if a.llm != "config":
                    extra += ["--llm", a.llm]
                if stage == "stage3" and a.resume_stage3_attempt is not None:
                    extra += ["--resume-attempt", str(a.resume_stage3_attempt)]
                r = run_agent_stage(stage, a.config, extra, cfg)
        except BaseException as exc:
            detail = f"{type(exc).__name__}: {exc}" or type(exc).__name__
            r = {"stage": stage, "finished": False, "workspace": str(run_dir(cfg, stage)),
                 "attempt_id": str(uuid.uuid4()), "interrupted": is_interruption(exc),
                 "failure": detail}
        if loops:
            r["after_repair_loop"] = len(loops)
        stage_report = write_stage_report(cfg, r, a.profile, preflight_report)
        print_report(stage_report, r["report_path"])
        results.append(r)
        persist_running_summary()
        report_stage(r, cfg)
        if r.get("finished"):
            for lp in loops:
                if lp["requested_by"] == stage and lp.get("resolved") is None:
                    lp["resolved"] = True              # the stage that asked for the repair passes now
            if queue and a.confirm_stages and not a.yes:
                nxt = queue[0]
                if not ask(f"Stage {STAGES[stage][0]} is done. Continue with Stage {STAGES[nxt][0]} ({STAGES[nxt][1]})?", a.yes):
                    stopped_after = stage
                    unanswered = not sys.stdin.isatty()
                    print(f"stopped after {STAGES[stage][1]}; to continue: {continue_command(a, nxt)}", flush=True)
                    for later in queue:
                        report_path = write_unstarted_stage_report(cfg, later, a.profile, preflight_report,
                                                                   "skipped", "stopped by user between stages")
                        results.append({"stage": later, "skipped": True, "stopped_by_user": True,
                                        "finished": False, "workspace": str(run_dir(cfg, later)),
                                        "report_path": report_path, "report_status": "skipped"})
                    persist_running_summary()
                    break
            continue
        up = (r.get("upstream") or {}).get("stage")
        if up and repair.valid_upstream(stage, up) is None:
            loop = {"loop": len(loops) + 1, "requested_by": stage, "stage": up, "reason": str(r.get("failure") or "")[:2000],
                    "evidence": (r.get("upstream") or {}).get("evidence"), "resolved": None}
            if reopened.get(up, 0) >= max_loops:
                loop["refused"] = f"{up} was already re-opened {reopened[up]} time(s) in this run (repair.max_loops_per_stage = {max_loops})"
                loop["resolved"] = False
                loops.append(loop)
                print(f"repair refused: {loop['refused']}", flush=True)
            elif not ask(f"{STAGES[stage][1]} found {STAGES[up][1]} wrong ({str(loop['evidence'] or loop['reason'])[:300]}). "
                         f"Re-open Stage {STAGES[up][0]} and run the stages after it again?", a.yes):
                loop["refused"] = ("the user stopped the run instead of re-opening the stage" if sys.stdin.isatty()
                                   else "no terminal to confirm the repair on (run with --yes to re-open stages automatically)")
                loop["resolved"] = False
                loops.append(loop)
            else:
                reopened[up] = reopened.get(up, 0) + 1
                loops.append(loop)
                k = loop["loop"]
                loop["archived_failure"] = repair.archive_failure(run_dir(cfg, stage), k)
                repair.write_request(run_dir(cfg, up), k, stage, up, loop["reason"], loop["evidence"] or "")
                rerun = ORDER[ORDER.index(up): ORDER.index(a.stop) + 1]
                for later in rerun[1:]:
                    if later != "stage4":
                        repair.write_revised(run_dir(cfg, later), k, up, stage, loop["reason"])
                loop["reruns"] = rerun
                print(f"repair loop {k}: {stage} found {up} wrong; re-opening {up}, then {' -> '.join(rerun[1:]) or 'nothing else'}", flush=True)
                queue = rerun
                continue
        ok = False
        for lp in loops:
            if lp.get("resolved") is None:
                lp["resolved"] = False
        for later in [s_ for s_ in ORDER[ORDER.index(stage) + 1: ORDER.index(a.stop) + 1]]:
            reason = f"blocked by failed stage {stage}"
            report_path = write_unstarted_stage_report(cfg, later, a.profile, preflight_report,
                                                       "blocked", reason)
            results.append({"stage": later, "blocked": True, "finished": False,
                            "workspace": str(run_dir(cfg, later)), "report_path": report_path,
                            "report_status": "blocked", "failure": reason})
        persist_running_summary()
        break
    summary_table(results)
    latest_results = latest_stage_results(results)
    # asset reuse: a run whose stages all passed joins examples/asset_reuse (examples.save_assets in the config)
    ran3 = bool(latest_results.get("stage3", {}).get("finished"))
    if ok and ran3 and not any(r.get("reused_example") for r in latest_results.values()):
        try:
            from agent.reuse import save_run
            saved = save_run(cfg, out_dir, name=name, video=cfg["inputs"].get("video"))
            if saved:
                print(f"saved as a reusable example: {saved['example']}")
        except Exception as e:
            print(f"could not save the run as an example: {type(e).__name__}: {e}")
    out_dir.mkdir(parents=True, exist_ok=True)
    requested_complete = stopped_after is None and all(
        stage in latest_results and not latest_results[stage].get("skipped")
        and latest_results[stage].get("finished") for stage in stages)
    full_pipeline_complete = requested_complete and a.start == "stage1" and a.stop == "stage4"
    warnings = preflight_report.get("status") == "warning" or any(
        r.get("report_status") == "warning" or r.get("stage4_status") == "passed_with_warnings"
        for r in latest_results.values())
    stage4_record = latest_results.get("stage4", {}).get("stage4_run_record") or {}
    stage4_release_ready = stage4_record.get("release_ready") is True
    if not ok:
        final_status = "failed"
    elif not requested_complete:
        final_status = "partial"
    elif a.profile == "smoke" and full_pipeline_complete:
        final_status = "smoke_complete"
    elif a.profile == "dataset_release" and full_pipeline_complete and not stage4_release_ready:
        final_status = "release_review_pending"
    elif warnings:
        final_status = "passed_with_warnings" if full_pipeline_complete else "scope_completed_with_warnings"
    else:
        final_status = "passed" if full_pipeline_complete else "scope_completed"
    summary = {"run": name, "profile": a.profile, "status": final_status,
               "ok": bool(ok and requested_complete), "scope_complete": requested_complete,
               "full_pipeline_complete": full_pipeline_complete, "requested_stages": stages,
               "preflight_report": str(preflight_path), "stages": results, "repair_loops": loops,
               "interrupted": any(r.get("interrupted") for r in latest_results.values()),
               **({"release_ready": stage4_release_ready} if a.profile == "dataset_release" and full_pipeline_complete else {}),
               **({"stopped_by_user_after": stopped_after} if stopped_after else {}),
               "finished_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    atomic_json(summary_path, summary)
    if not ok:
        # the run's own failure report: the stage that failed, why, and every repair loop tried on the way
        failed = next((r for r in reversed(results)
                       if not r.get("finished") and not r.get("skipped") and not r.get("blocked")), {})
        (out_dir / "failure_report.json").write_text(json.dumps({
            "run": name, "failed_stage": failed.get("stage"), "reason": failed.get("failure"), "upstream": failed.get("upstream"),
            "interrupted": failed.get("interrupted", False),
            "stage_failure_report": str(Path(failed["workspace"]) / "failure_report.json") if failed.get("workspace") else None,
            "repair_loops": loops, "stages": results}, indent=1, default=str))
    elif (out_dir / "failure_report.json").exists():
        (out_dir / "failure_report.json").replace(out_dir / "failure_report_previous.json")
    print(f"summary: {out_dir / 'run_summary.json'}")
    if ok and unanswered:
        return 2                                   # stopped after a stage because nobody could be asked; not a full run
    return 0 if ok and requested_complete else (2 if ok else 1)


def main(argv=None):
    """Turn top-level SIGTERM into the same interrupt path as Ctrl-C, then restore the host handler."""
    previous_handler = signal.getsignal(signal.SIGTERM)

    def sigterm_as_interrupt(signum, _frame):
        raise KeyboardInterrupt(f"received signal {signum}")

    signal.signal(signal.SIGTERM, sigterm_as_interrupt)
    try:
        return _main_impl(argv)
    finally:
        signal.signal(signal.SIGTERM, previous_handler)


if __name__ == "__main__":
    def _reexec_configured_python(argv):
        if os.environ.get("DEXAGENT_ENV_SWITCHED") == "1" or os.environ.get("DEXAGENT_NO_ENV_SWITCH") == "1":
            return
        value_flags = {"--from", "--to", "--name", "--video", "--prompt", "--max-rounds", "--profile", "--llm",
                       "--resume-stage3-attempt", "--backend-eval", "--reference-run", "--simfoundry-stage8",
                       "--simfoundry-stage11", "--simfoundry-stage12", "--simfoundry-pose-info", "--simfoundry-collision"}
        config = None
        skip = False
        for value in argv:
            if skip:
                skip = False
                continue
            if value in value_flags:
                skip = True
                continue
            if value in {"--help", "-h"}:
                return
            if value.startswith("-"):
                continue
            config = value
            break
        cfg = load_config(config or os.environ.get("DEXAGENT_CONFIG"))
        configured = str((cfg.get("environments") or {}).get("pipeline") or "")
        if configured and Path(configured).is_file() and Path(configured).resolve() != Path(sys.executable).resolve():
            print(f"Switching to configured pipeline Python: {configured}", flush=True)
            env = dict(os.environ)
            env["DEXAGENT_ENV_SWITCHED"] = "1"
            os.execvpe(configured, [configured, str(Path(__file__).resolve()), *argv], env)

    _reexec_configured_python(sys.argv[1:])
    sys.exit(main())
