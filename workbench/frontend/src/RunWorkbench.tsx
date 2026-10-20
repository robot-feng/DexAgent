import { useEffect, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  ArrowUpRight,
  Box,
  ChevronDown,
  CircleCheck,
  CircleDot,
  Clock3,
  FileText,
  Film,
  Image,
  Layers3,
  Terminal,
} from "lucide-react";
import { api, contentUrl } from "./api";
import type { Artifact, RunDetail } from "./api";
import {
  ArtifactDialog,
  Badge,
  Empty,
  ErrorNotice,
  formatTime,
  GateReport,
  Loading,
  label,
  Media,
  pretty,
} from "./components";
import { ArtifactList, EpisodeList } from "./pages";
import NativeScene from "./NativeScene";
import PlanningDiagnostics from "./PlanningDiagnostics";

const tabs = [
  ["overview", "概览"],
  ["scene", "场景"],
  ["trajectory", "轨迹与回放"],
  ["planning", "规划诊断"],
  ["validation", "验收"],
  ["artifacts", "产物"],
];

function objectRecords(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.filter(
        (v): v is Record<string, unknown> =>
          v !== null && typeof v === "object",
      )
    : [];
}

function Overview({
  run,
  open,
  selectedMedia,
  chooseMedia,
}: {
  run: RunDetail;
  open: (a: Artifact) => void;
  selectedMedia?: string | null;
  chooseMedia: (id: string) => void;
}) {
  const media = [...(run.input_video ? [run.input_video] : []), ...run.media];
  const selected = media.find((a) => a.id === selectedMedia) ?? media[0];
  const subgoals = objectRecords(run.semantics?.subgoals);
  return (
    <div className="overview">
      <div className="viewer-head">
        <div>
          <span className="eyebrow">REFERENCE / RESULTS</span>
          <h2>
            {selected?.id === run.input_video?.id
              ? "原始演示"
              : (selected?.name ?? "运行产物")}
          </h2>
        </div>
        {selected && (
          <button className="link-button" onClick={() => open(selected)}>
            展开预览 <ArrowUpRight size={14} />
          </button>
        )}
      </div>
      <div className="media-viewport">
        {selected ? (
          <Media artifact={selected} />
        ) : (
          <Empty
            title="尚无可预览媒体"
            detail="可在验收和产物页查看当前报告与诊断材料。"
          />
        )}
      </div>
      {media.length > 0 && (
        <div className="media-strip">
          {media.map((a, i) => (
            <button
              key={a.id}
              className={`media-choice ${selected?.id === a.id ? "selected" : ""}`}
              onClick={() => chooseMedia(a.id)}
            >
              {a.kind === "video" ? <Film size={16} /> : <Image size={16} />}
              <span>
                {a.id === run.input_video?.id
                  ? "原始视频"
                  : a.path.includes("/original/")
                    ? `原始布局 · ${a.name}`
                    : a.path.includes("/0000/")
                      ? `随机布局 · ${a.name}`
                      : a.name}
              </span>
              <small>{String(i + 1).padStart(2, "0")}</small>
            </button>
          ))}
        </div>
      )}
      {media.some(
        (a) => a.name === "compare.mp4" || a.name === "comparison.mp4",
      ) && (
        <p className="media-caption">
          真人/仿真对照用于视觉比较；没有明确时间映射时，不表示两段视频逐帧同步。
        </p>
      )}
      <section className="task-section">
        <div className="section-heading">
          <div>
            <p className="eyebrow">TASK / SUBGOALS</p>
            <h2>任务目标</h2>
          </div>
          <span className="source-tag">原生语义</span>
        </div>
        <p className="task-description">
          {run.task || "该运行未记录任务目标，可查看原始报告。"}
        </p>
        {subgoals.length > 0 && (
          <div className="subgoals">
            {subgoals.map((goal, i) => (
              <div className="subgoal" key={i}>
                <span className="number">{String(i + 1).padStart(2, "0")}</span>
                <div>
                  <strong>
                    {typeof goal.goal === "string" ? goal.goal : pretty(goal)}
                  </strong>
                  {typeof goal.motion === "string" && (
                    <p className="small muted">{goal.motion}</p>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
      {run.sessions.length > 0 && (
        <section className="task-section">
          <p className="eyebrow">INVOCATION HISTORY</p>
          <h2>调用与恢复记录</h2>
          <div className="session-timeline">
            {run.sessions.map((session, i) => (
              <details key={i}>
                <summary>
                  <CircleDot size={15} />
                  <span>
                    {typeof session.name === "string"
                      ? session.name
                      : `调用 ${i + 1}`}
                  </span>
                  <ChevronDown size={14} />
                </summary>
                <pre>{pretty(session)}</pre>
              </details>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

function LogConsole({ runId }: { runId: string }) {
  const logs = useQuery({
    queryKey: ["logs", runId],
    queryFn: () => api.logs(runId),
  });
  const [selected, setSelected] = useState("");
  const [expanded, setExpanded] = useState(false);
  const [paused, setPaused] = useState(false);
  const [tail, setTail] = useState(true);
  const body = useRef<HTMLDivElement>(null);
  const file =
    logs.data?.items.find((a) => a.id === selected) ?? logs.data?.items[0];
  const text = useQuery({
    queryKey: ["log-text", file?.id, tail],
    queryFn: () => api.text(file!.id, tail ? -1 : 0),
    enabled: expanded && !!file,
    refetchInterval: expanded && !paused && tail ? 3_000 : false,
    refetchOnWindowFocus: !paused,
  });
  useEffect(() => {
    if (tail && !paused && body.current)
      body.current.scrollTop = body.current.scrollHeight;
  }, [text.data, tail, paused]);
  return (
    <section className={`log-console ${expanded ? "expanded" : ""}`}>
      <div className="console-toolbar">
        <button
          className="console-title"
          aria-expanded={expanded}
          onClick={() => setExpanded(!expanded)}
        >
          <Terminal size={15} />
          日志与事件 <ChevronDown size={14} />
        </button>
        {expanded && (
          <select
            aria-label="选择日志"
            value={file?.id || ""}
            onChange={(e) => {
              setSelected(e.target.value);
              setPaused(false);
            }}
          >
            {logs.data?.items.map((a) => (
              <option key={a.id} value={a.id}>
                {a.path}
              </option>
            ))}
          </select>
        )}
        {expanded && (
          <button
            className="console-title"
            onClick={() => {
              setTail(!tail);
              setPaused(false);
            }}
          >
            {tail ? "从开头" : "查看末尾"}
          </button>
        )}
        {expanded && tail && (
          <button className="console-title" onClick={() => setPaused(!paused)}>
            {paused ? "跟随末尾" : "暂停跟随"}
          </button>
        )}
        <span className="small muted">原生文件 · 只读</span>
      </div>
      {expanded && (
        <div
          className="console-body"
          ref={body}
          onScroll={() => {
            const node = body.current;
            if (
              tail &&
              node &&
              node.scrollHeight - node.scrollTop - node.clientHeight > 48
            )
              setPaused(true);
          }}
        >
          <ErrorNotice error={logs.error ?? text.error} />
          {text.isLoading ? (
            <Loading />
          ) : file ? (
            <>
              <pre>{text.data?.text}</pre>
              {text.data && !text.data.eof && (
                <p className="small muted">
                  当前预览{tail ? "末尾" : "开头"} 64 KiB ·{" "}
                  <a href={contentUrl(file, true)}>下载完整日志</a>
                </p>
              )}
            </>
          ) : (
            <Empty title="没有可用日志" />
          )}
        </div>
      )}
    </section>
  );
}

export function RunWorkbench() {
  const { runId = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const [preview, setPreview] = useState<Artifact | null>(null);
  const runQuery = useQuery({
    queryKey: ["run", runId],
    queryFn: () => api.run(runId),
    refetchInterval: 10_000,
  });
  const projects = useQuery({ queryKey: ["projects"], queryFn: api.projects });
  const executions = useQuery({
    queryKey: ["executions"],
    queryFn: api.executions,
    refetchInterval: 5000,
  });
  const managed = executions.data?.find((job) => job.run_id === runId);
  const client = useQueryClient();
  const assign = useMutation({
    mutationFn: (project: string) => api.assign(runQuery.data!.name, project),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["run", runId] });
      client.invalidateQueries({ queryKey: ["runs"] });
      client.invalidateQueries({ queryKey: ["projects"] });
    },
  });
  useEffect(() => {
    if (runQuery.data) localStorage.setItem("lastRun", runId);
  }, [runId, runQuery.data]);
  const update = (values: Record<string, string>) => {
    const n = new URLSearchParams(params);
    for (const [k, v] of Object.entries(values)) v ? n.set(k, v) : n.delete(k);
    setParams(n);
  };
  if (runQuery.isLoading) return <Loading />;
  if (runQuery.error)
    return (
      <div className="page">
        <Link to="/projects">← 返回运行列表</Link>
        <ErrorNotice error={runQuery.error} />
      </div>
    );
  const run = runQuery.data;
  if (!run) return null;
  const stage =
    run.stages.find((s) => s.id === params.get("stage")) ??
    run.stages.find((s) => s.report?.validation === "failed") ??
    run.stages[0];
  const reports = stage
    ? [
        ...(stage.report ? [stage.report] : []),
        ...stage.steps,
        ...stage.history,
      ]
    : [];
  const report =
    reports.find((r) => r.artifact.id === params.get("report")) ??
    stage?.report;
  const view = tabs.some((t) => t[0] === params.get("view"))
    ? params.get("view")
    : "overview";
  return (
    <main className="run-page">
      <div className="run-header">
        <div className="run-heading">
          <Link
            className="icon-button"
            aria-label="返回运行列表"
            to="/projects"
          >
            <ArrowLeft size={18} />
          </Link>
          <div>
            <div className="run-eyebrow">
              <span className="eyebrow">
                {run.source === "robosimstudio" ? "ROBOSIMSTUDIO" : "DEXAGENT"}{" "}
                / RUN
              </span>
              <span className="source-tag">
                {run.profile ?? "未记录 profile"}
              </span>
            </div>
            <h1>{run.name}</h1>
          </div>
        </div>
        <div className="run-header-right">
          {managed && (
            <Link className="button" to={`/executions?job=${managed.id}`}>
              查看执行进程
            </Link>
          )}
          {run.available_actions.includes("adjust_layout") && (
            <Link
              className="button"
              to={`/executions?mode=candidate&source=${run.id}`}
            >
              调整物体布局
            </Link>
          )}
          <select
            aria-label="归属项目"
            title="归属项目"
            value={run.project_id}
            disabled={assign.isPending || !projects.data}
            onChange={(event) => assign.mutate(event.target.value)}
          >
            {projects.data?.map((project) => (
              <option key={project.id} value={project.id}>
                {project.name}
              </option>
            ))}
          </select>
          <Badge value={run.validation} />
          <span className="small muted">
            <Clock3 size={13} />
            {formatTime(run.updated_at)}
          </span>
        </div>
      </div>
      <ErrorNotice error={assign.error ?? projects.error} />
      <div className="run-scope">
        <span className="scope-item">
          <CircleCheck size={14} />
          {run.scope.scope_complete
            ? "本次范围完成"
            : "执行状态：" + label(run.execution)}
          {run.scope.requested_stages.length > 0 && (
            <b>{run.scope.requested_stages.join(" → ")}</b>
          )}
        </span>
        <span className="scope-item">
          单次全流程：
          {run.scope.single_invocation_full_pipeline_complete === true
            ? "完成"
            : run.scope.single_invocation_full_pipeline_complete === false
              ? "未完成"
              : "未记录"}
        </span>
        <span className="scope-item">
          跨调用证据：
          {run.scope.evidence_complete_across_invocations === true
            ? "完整"
            : run.scope.evidence_complete_across_invocations === false
              ? "不完整"
              : "未汇总"}
        </span>
        <span className="scope-item">
          发布资格：{run.release === "ready" ? "源报告标记就绪" : "未确认"}
        </span>
      </div>
      <div className="run-layout">
        <aside className="stage-rail">
          <div className="rail-title">
            <Layers3 size={15} />
            阶段与尝试 <span>{run.stages.length}</span>
          </div>
          {run.stages.map((s, i) => (
            <div className="stage-group" key={s.id}>
              <button
                className={`stage-button ${stage?.id === s.id ? "selected" : ""}`}
                onClick={() => update({ stage: s.id, report: "" })}
              >
                <span
                  className={`stage-number ${s.report?.validation ?? "unknown"}`}
                >
                  {s.report?.validation === "passed" ? <CheckIcon /> : i + 1}
                </span>
                <span>
                  <strong>{s.title}</strong>
                  <small>
                    {s.report?.validation === "passed"
                      ? "检查通过"
                      : s.report?.validation === "warning"
                        ? "通过 · 保留警告"
                        : (s.report?.source_status ?? "暂无验收报告")}
                  </small>
                </span>
                {s.report?.elapsed_s != null && (
                  <span className="stage-duration">
                    {s.report.elapsed_s >= 60
                      ? `${Math.round(s.report.elapsed_s / 60)}m`
                      : `${Math.round(s.report.elapsed_s)}s`}
                  </span>
                )}
              </button>
              {stage?.id === s.id && s.steps.length > 0 && (
                <div className="stage-steps">
                  {s.steps.map((step) => (
                    <button
                      key={step.artifact.id}
                      onClick={() =>
                        update({ report: step.artifact.id, view: "validation" })
                      }
                    >
                      <i className={step.validation} />
                      {step.artifact.name.replace(".json", "")}
                    </button>
                  ))}
                </div>
              )}
              {stage?.id === s.id && s.history.length > 0 && (
                <details className="stage-history">
                  <summary>{s.history.length} 条历史报告</summary>
                  {s.history.map((r, j) => (
                    <button
                      key={r.artifact.id}
                      onClick={() =>
                        update({ report: r.artifact.id, view: "validation" })
                      }
                    >
                      <span className="mono">
                        {r.attempt_id?.slice(0, 8) || `记录 ${j + 1}`}
                      </span>
                      <Badge value={r.validation} />
                    </button>
                  ))}
                </details>
              )}
            </div>
          ))}
          {run.stages.length === 0 && (
            <p className="muted small rail-empty">没有可识别的阶段报告</p>
          )}
          <div className="rail-foot">
            <span className="eyebrow">SOURCE STATUS</span>
            <code>{run.source_status ?? "unknown"}</code>
          </div>
        </aside>
        <section className="run-center">
          <div className="view-tabs" role="tablist" aria-label="运行视图">
            {tabs.map(([id, title], index) => (
              <button
                role="tab"
                id={`run-tab-${id}`}
                aria-controls="run-content"
                tabIndex={view === id ? 0 : -1}
                aria-selected={view === id}
                className={view === id ? "selected" : ""}
                key={id}
                onClick={() => update({ view: id })}
                onKeyDown={(event) => {
                  const next =
                    event.key === "ArrowRight"
                      ? (index + 1) % tabs.length
                      : event.key === "ArrowLeft"
                        ? (index + tabs.length - 1) % tabs.length
                        : event.key === "Home"
                          ? 0
                          : event.key === "End"
                            ? tabs.length - 1
                            : null;
                  if (next === null) return;
                  event.preventDefault();
                  update({ view: tabs[next][0] });
                  document.getElementById(`run-tab-${tabs[next][0]}`)?.focus();
                }}
              >
                {title}
              </button>
            ))}
          </div>
          <div
            className="view-content"
            id="run-content"
            role="tabpanel"
            aria-labelledby={`run-tab-${view}`}
          >
            {run.issues.length > 0 && (
              <details className="notice danger">
                <summary>{run.issues.length} 个读取/证据问题</summary>
                {run.issues.map((issue, i) => (
                  <p key={i}>
                    <code>{issue.path}</code>
                    <br />
                    {issue.message}
                  </p>
                ))}
              </details>
            )}
            {view === "overview" && (
              <Overview
                run={run}
                open={setPreview}
                selectedMedia={params.get("asset")}
                chooseMedia={(id) => update({ asset: id })}
              />
            )}
            {view === "validation" &&
              (report ? (
                <GateReport report={report} open={setPreview} />
              ) : (
                <Empty
                  title="该阶段尚无验收报告"
                  detail="没有证据时，不推断为成功。"
                />
              ))}
            {view === "artifacts" && (
              <ArtifactList runId={run.id} key={run.id} />
            )}
            {view === "scene" && (
              <>
                {run.recorded_3d_available && (
                  <NativeScene runId={run.id} key={run.id} />
                )}
                <div className="notice">
                  <Box size={18} />
                  <div>
                    <strong>原生场景与资产</strong>
                    <p>
                      原始文件与验收证据可追溯。记录回放不会改变仿真状态；实时场景编辑另行接入。
                    </p>
                  </div>
                </div>
                <ArtifactList runId={run.id} kind="model" key={run.id} />
              </>
            )}
            {view === "trajectory" && (
              <>
                <div className="section-heading">
                  <div>
                    <p className="eyebrow">NATIVE EPISODE / REPLAY</p>
                    <h2>原生记录与回放</h2>
                  </div>
                </div>
                <EpisodeList runId={run.id} key={run.id} />
                <div className="task-section">
                  <h2>仿真与合成视频</h2>
                  <div className="media-strip">
                    {run.media
                      .filter((a) => a.kind === "video")
                      .map((a) => (
                        <button
                          className="media-choice"
                          key={a.id}
                          onClick={() => setPreview(a)}
                        >
                          <Film size={15} />
                          <span>
                            {a.path.includes("/original/") ? "原始布局 / " : ""}
                            {a.name}
                          </span>
                        </button>
                      ))}
                  </div>
                </div>
              </>
            )}
            {view === "planning" && (
              <PlanningDiagnostics runId={run.id} open={setPreview} />
            )}
          </div>
          <LogConsole runId={run.id} key={run.id} />
        </section>
        <aside className="inspector">
          <div className="rail-title">
            <FileText size={14} />
            检查器
          </div>
          <section>
            <p className="eyebrow">SELECTED STAGE</p>
            <h3>{stage?.title ?? "运行"}</h3>
            <Badge value={report?.validation} />
            {report?.elapsed_s != null && (
              <p className="small muted">
                耗时 {report.elapsed_s.toFixed(1)} s
              </p>
            )}
          </section>
          <section>
            <h4>检查摘要</h4>
            <div className="inspector-stats">
              <span>
                <b>{report?.checks.filter((c) => c.required).length ?? "—"}</b>
                必需检查
              </span>
              <span>
                <b>
                  {report?.checks.filter(
                    (c) => c.status === "warning" || c.status === "failed",
                  ).length ?? "—"}
                </b>
                警告 / 失败
              </span>
            </div>
            <button
              className="button full"
              onClick={() => update({ view: "validation" })}
            >
              查看检查与证据 <ArrowUpRight size={14} />
            </button>
          </section>
          {stage?.progress && (
            <section>
              <h4>Agent 进度快照</h4>
              {stage.progress_stale && (
                <p className="small amber">正在更新，显示上次有效快照。</p>
              )}
              <dl>
                <dt>轮次</dt>
                <dd>
                  {pretty(stage.progress.round)} /{" "}
                  {pretty(stage.progress.max_rounds)}
                </dd>
                <dt>工具调用</dt>
                <dd>{pretty(stage.progress.tool_calls)}</dd>
              </dl>
              <p className="small muted">进度快照不是最终验收结论。</p>
            </section>
          )}
          {report && (
            <section>
              <h4>来源身份</h4>
              <code className="hash">{report.sha256}</code>
              <p className="small muted">
                {report.schema_id ?? "未记录 schema"}
              </p>
              <button
                className="link-button"
                onClick={() => setPreview(report.artifact)}
              >
                原始报告 ↗
              </button>
            </section>
          )}
          <section>
            <h4>运行汇总</h4>
            {run.reports.map((a) => (
              <button
                className="report-link"
                key={a.id}
                onClick={() => setPreview(a)}
              >
                <FileText size={13} />
                {a.name}
              </button>
            ))}
          </section>
          <section className="inspector-note">
            本工作台读取原生产物。执行状态、机器检查和数据发布资格分别展示。
          </section>
        </aside>
      </div>
      {preview && (
        <ArtifactDialog artifact={preview} close={() => setPreview(null)} />
      )}
    </main>
  );
}

function CheckIcon() {
  return <CircleCheck size={16} />;
}
