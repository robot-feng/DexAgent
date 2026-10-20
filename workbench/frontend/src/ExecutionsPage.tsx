import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { Copy, PlayCircle, RotateCcw, Square } from "lucide-react";
import { api, type Execution, type ExecutionCreate } from "./api";
import { Badge, ErrorNotice, Loading, formatTime } from "./components";

const modes: Record<Execution["mode"], string> = {
  pipeline: "视频 → 任务与数据流程",
  simfoundry: "SimFoundry 刚体资产验收",
  reference: "已有 DexAgent 轨迹在 RSS 中验收",
  grasp: "RSS Sharpa 抓取验收",
  sharpa: "RSS Sharpa 运动验收",
  rigid: "RSS 刚体基础验收",
  towel: "RSS 毛巾示例",
};
const statuses: Record<Execution["status"], string> = {
  running: "进程运行中",
  completed: "进程已结束",
  failed: "执行失败",
  stopped: "已停止",
  interrupted: "退出状态未取得",
};

export function ExecutionsPage() {
  const client = useQueryClient();
  const [copied, setCopied] = useState<string | null>(null);
  const [copyError, setCopyError] = useState<Error | null>(null);
  const [params, setParams] = useSearchParams();
  const [form, setForm] = useState<ExecutionCreate>({
    config_id: "",
    mode: "pipeline",
    profile: "pipeline_eval",
    stop_at: "stage4",
    video: "",
    prompt: "",
    reference_run_id: null,
    stage8: "",
    stage11: "",
    stage12: "",
    no_render: false,
  });
  const mode = form.mode ?? "pipeline";
  const patch = <K extends keyof ExecutionCreate>(
    key: K,
    value: ExecutionCreate[K],
  ) => setForm((old) => ({ ...old, [key]: value }));
  const catalog = useQuery({
    queryKey: ["execution-catalog"],
    queryFn: api.executionCatalog,
  });
  const jobs = useQuery({
    queryKey: ["executions"],
    queryFn: api.executions,
    refetchInterval: 2000,
  });
  const referenceRuns = useQuery({
    queryKey: ["execution-references"],
    queryFn: () => api.runs("limit=200"),
    enabled: mode === "reference",
  });
  const selected = params.get("job")
    ? jobs.data?.find((job) => job.id === params.get("job"))
    : jobs.data?.[0];
  const nativeRun = useQuery({
    queryKey: ["execution-run", selected?.run_id],
    queryFn: () => api.run(selected!.run_id!),
    enabled: !!selected?.run_id,
    refetchInterval: selected?.status === "running" ? 2000 : false,
  });
  const nativeChecks =
    nativeRun.data?.stages.flatMap(
      (stage) => stage.report?.checks.filter((check) => check.required) ?? [],
    ) ?? [];
  const logs = useQuery({
    queryKey: ["execution-log", selected?.id],
    queryFn: () => api.executionLog(selected!.id),
    enabled: !!selected,
    refetchInterval: selected?.status === "running" ? 2000 : false,
  });
  const focus = (job: Execution) => {
    client.setQueryData<Execution[]>(["executions"], (old) => [
      job,
      ...(old ?? []).filter((j) => j.id !== job.id),
    ]);
    setParams({ job: job.id });
    client.invalidateQueries({ queryKey: ["health"] });
    client.invalidateQueries({ queryKey: ["overview-runs"] });
  };
  const create = useMutation({
    mutationFn: api.createExecution,
    onSuccess: focus,
  });
  const action = useMutation({
    mutationFn: ({ id, kind }: { id: string; kind: "stop" | "retry" }) =>
      kind === "stop" ? api.stopExecution(id) : api.retryExecution(id),
    onSuccess: (job) => {
      focus(job);
      client.invalidateQueries({ queryKey: ["execution-log"] });
    },
  });
  const active = jobs.data?.some((job) => job.status === "running");
  const busy = create.isPending || action.isPending;
  const configId = form.config_id || catalog.data?.configs[0]?.id || "";
  return (
    <div className="page cleanup-page execution-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">RUN / OBSERVE / VERIFY</p>
          <h1>任务执行</h1>
          <p className="muted">
            从配置启动流程，查看日志、停止执行，或保留原结果创建重试。
          </p>
        </div>
      </div>
      <ErrorNotice error={catalog.error ?? jobs.error} />
      <div className="cleanup-layout">
        <section className="panel cleanup-form-panel">
          <h2>新建运行</h2>
          <form
            className="cleanup-form"
            onSubmit={(event) => {
              event.preventDefault();
              create.mutate({ ...form, config_id: configId });
            }}
          >
            <label>
              任务类型
              <select
                value={mode}
                onChange={(e) =>
                  patch("mode", e.target.value as Execution["mode"])
                }
              >
                {(catalog.data?.modes ?? []).map((m) => (
                  <option key={m} value={m}>
                    {modes[m]}
                  </option>
                ))}
              </select>
            </label>
            <label>
              运行配置
              <select
                required
                value={configId}
                onChange={(e) => patch("config_id", e.target.value)}
              >
                {!catalog.data?.configs.length && (
                  <option value="">暂无配置</option>
                )}
                {catalog.data?.configs.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </label>
            {mode === "pipeline" && (
              <>
                <label>
                  输入视频路径
                  <input
                    value={form.video ?? ""}
                    onChange={(e) => patch("video", e.target.value)}
                    placeholder="留空使用配置中的视频"
                  />
                </label>
                <label>
                  任务描述
                  <textarea
                    value={form.prompt ?? ""}
                    onChange={(e) => patch("prompt", e.target.value)}
                    placeholder="留空使用配置中的任务描述"
                  />
                </label>
                <div className="cleanup-form-row">
                  <label>
                    执行配置
                    <select
                      value={form.profile}
                      onChange={(e) =>
                        patch(
                          "profile",
                          e.target.value as ExecutionCreate["profile"],
                        )
                      }
                    >
                      <option value="pipeline_eval">正式流程</option>
                      <option value="smoke">冒烟测试（可复用示例）</option>
                      <option value="dataset_release">数据发布验收</option>
                    </select>
                  </label>
                  <label>
                    执行到
                    <select
                      value={form.stop_at}
                      onChange={(e) =>
                        patch(
                          "stop_at",
                          e.target.value as ExecutionCreate["stop_at"],
                        )
                      }
                    >
                      <option value="stage1">任务理解</option>
                      <option value="stage2">场景重建</option>
                      <option value="stage3">动作生成</option>
                      <option value="stage4">数据生成</option>
                    </select>
                  </label>
                </div>
                <p className="small muted">
                  沿用配置中的 Agent 后端。使用 external 后端时，需要 Agent
                  持续响应请求。
                </p>
              </>
            )}
            {mode === "reference" && (
              <label>
                参考运行
                <select
                  required
                  value={form.reference_run_id ?? ""}
                  onChange={(e) => patch("reference_run_id", e.target.value)}
                >
                  <option value="">选择已有 DexAgent 运行</option>
                  {referenceRuns.data?.items.map((r) => (
                    <option key={r.id} value={r.id}>
                      {r.name}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {mode === "reference" && (
              <ErrorNotice error={referenceRuns.error} />
            )}
            {mode === "simfoundry" && (
              <>
                {(["stage8", "stage11", "stage12"] as const).map((key) => (
                  <label key={key}>
                    {key === "stage8"
                      ? "Stage 8 几何与姿态目录"
                      : key === "stage11"
                        ? "Stage 11 物理资产目录"
                        : "Stage 12 世界位姿目录"}
                    <input
                      required
                      value={form[key] ?? ""}
                      onChange={(e) => patch(key, e.target.value)}
                      placeholder={
                        key === "stage8"
                          ? "例如 SimFoundry/Data/scene/s8_pose"
                          : key === "stage11"
                            ? "例如 SimFoundry/Data/scene/s11_sim"
                            : "例如 SimFoundry/Data/scene/s12_physics"
                      }
                    />
                  </label>
                ))}
              </>
            )}
            {mode !== "pipeline" && (
              <div className="cleanup-model-row">
                <label className="check-row">
                  <input
                    type="checkbox"
                    checked={form.no_render}
                    onChange={(e) => patch("no_render", e.target.checked)}
                  />
                  跳过离线视频渲染
                </label>
              </div>
            )}
            <ErrorNotice error={create.error} />
            {catalog.data && !catalog.data.available && (
              <div className="notice">{catalog.data.detail}</div>
            )}
            {active && (
              <p className="small muted">
                已有任务运行中。等待它结束或停止后，可启动下一次运行。
              </p>
            )}
            <button
              className="button primary"
              disabled={busy || active || !catalog.data?.available || !configId}
              type="submit"
            >
              <PlayCircle size={15} />
              {create.isPending ? "正在启动…" : "启动运行"}
            </button>
          </form>
        </section>
        <section className="panel cleanup-results-panel">
          <h2>执行记录</h2>
          {jobs.isLoading && <Loading />}
          {!jobs.isLoading && params.get("job") && !selected && (
            <div className="notice" role="status">
              该运行记录不存在。
              <button
                type="button"
                className="button"
                onClick={() => setParams({})}
              >
                查看最近运行
              </button>
            </div>
          )}
          {!jobs.isLoading && !jobs.data?.length && (
            <p className="cleanup-empty">还没有从工作台启动的任务。</p>
          )}
          <div className="cleanup-job-list">
            {jobs.data?.slice(0, 30).map((job) => (
              <button
                key={job.id}
                className={`cleanup-job-row ${selected?.id === job.id ? "selected" : ""}`}
                onClick={() => setParams({ job: job.id })}
              >
                <span
                  className={`cleanup-job-mark ${job.status === "running" ? "preparing" : job.status === "failed" ? "failed" : ""}`}
                />
                <span>
                  <strong>{modes[job.mode]}</strong>
                  <small className="muted">
                    {formatTime(job.created_at)} · {job.config_name}
                  </small>
                </span>
                <span className="small">
                  {job.agent_wait ? "等待 Agent" : statuses[job.status]}
                </span>
              </button>
            ))}
          </div>
          {selected && (
            <div className="cleanup-review">
              <div className="cleanup-review-header">
                <div>
                  <h3>{selected.output_name}</h3>
                  <span className="cleanup-status">
                    {statuses[selected.status]}
                  </span>
                </div>
              </div>
              <p className="small muted">{selected.message}</p>
              <ErrorNotice error={nativeRun.error} />
              {nativeRun.data && (
                <section
                  className="execution-native-result"
                  aria-label="原生验收结果"
                >
                  <span>
                    原生验收 <Badge value={nativeRun.data.validation} />
                  </span>
                  {nativeChecks.length > 0 && (
                    <span>
                      必需检查{" "}
                      {
                        nativeChecks.filter(
                          (check) => check.status === "passed",
                        ).length
                      }
                      /{nativeChecks.length}
                    </span>
                  )}
                  <span>
                    {nativeRun.data.release === "ready"
                      ? "源报告标记具备发布资格"
                      : "发布资格未确认"}
                  </span>
                </section>
              )}
              {selected.agent_wait && (
                <div className="notice execution-agent-wait" role="status">
                  <div>
                    <strong>
                      等待 Agent 处理第 {selected.agent_wait.turn} 轮请求
                    </strong>
                    <p>将当前请求路径交给 Agent 处理，收到响应后流程会继续。</p>
                    <code className="execution-request-path">
                      {selected.agent_wait.request_path}
                    </code>
                    <button
                      type="button"
                      className="button"
                      onClick={async () => {
                        try {
                          await navigator.clipboard.writeText(
                            selected.agent_wait!.request_path,
                          );
                          setCopied(selected.agent_wait!.request_id);
                          setCopyError(null);
                        } catch {
                          setCopyError(
                            new Error("复制失败，请选中并复制上方请求路径。"),
                          );
                        }
                      }}
                    >
                      <Copy size={14} />
                      {copied === selected.agent_wait.request_id
                        ? "已复制"
                        : "复制 Agent 请求路径"}
                    </button>
                    <ErrorNotice error={copyError} />
                  </div>
                </div>
              )}
              {selected.retry_of && (
                <p className="small muted">
                  本次重试使用原配置快照，并写入新运行目录。
                </p>
              )}
              <div className="cleanup-actions">
                {selected.run_id && (
                  <Link
                    className="button primary"
                    to={`/runs/${selected.run_id}`}
                  >
                    查看运行与验收
                  </Link>
                )}
                {selected.status === "running" ? (
                  <button
                    className="button"
                    disabled={busy}
                    onClick={() =>
                      action.mutate({ id: selected.id, kind: "stop" })
                    }
                  >
                    <Square size={14} />
                    停止运行
                  </button>
                ) : (
                  <button
                    className="button"
                    disabled={busy || active}
                    onClick={() =>
                      action.mutate({ id: selected.id, kind: "retry" })
                    }
                  >
                    <RotateCcw size={14} />
                    以新运行重试
                  </button>
                )}
              </div>
              <ErrorNotice error={action.error ?? logs.error} />
              <p className="small muted">
                进程退出与任务通过分别记录；训练发布资格以运行页的验收证据为准。
              </p>
              <details open>
                <summary>执行日志（最近 64 KiB）</summary>
                <pre
                  className="text-preview"
                  style={{ maxHeight: 420, overflow: "auto" }}
                >
                  {logs.data?.text || "等待日志输出…"}
                </pre>
              </details>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
