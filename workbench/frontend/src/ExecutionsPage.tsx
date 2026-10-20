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
  candidate: "任务场景候选 · RSS 落稳验收",
  replan: "基于已验收候选重新规划任务",
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
    mode:
      params.get("mode") === "replan"
        ? "replan"
        : params.get("mode") === "candidate"
          ? "candidate"
          : "pipeline",
    profile: "pipeline_eval",
    stop_at: params.get("mode") === "replan" ? "stage3" : "stage4",
    video: "",
    prompt: "",
    reference_run_id: params.get("source"),
    scene_offsets: [],
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
    enabled: ["reference", "candidate", "replan"].includes(mode),
  });
  const configId = form.config_id || catalog.data?.configs[0]?.id || "";
  const candidateScene = useQuery({
    queryKey: ["candidate-scene", form.reference_run_id, configId],
    queryFn: () => api.executionScene(form.reference_run_id!, configId),
    enabled: mode === "candidate" && !!form.reference_run_id && !!configId,
    retry: false,
    refetchOnWindowFocus: false,
  });
  const selected = params.get("job")
    ? jobs.data?.find((job) => job.id === params.get("job"))
    : jobs.data?.[0];
  const taskInputs = useQuery({
    queryKey: ["task-variant-inputs", form.reference_run_id],
    queryFn: () => api.executionTaskInputs(form.reference_run_id!),
    enabled: mode === "replan" && !!form.reference_run_id,
    retry: false,
    refetchOnWindowFocus: false,
  });
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
              create.mutate({
                ...form,
                config_id: configId,
                scene_sha256:
                  mode === "candidate" ? candidateScene.data?.sha256 : null,
                scene_offsets: mode === "candidate" ? form.scene_offsets : [],
                task_inputs_sha256:
                  mode === "replan" ? taskInputs.data?.sha256 : null,
                video: mode === "replan" ? "" : form.video,
                prompt: mode === "replan" ? "" : form.prompt,
              });
            }}
          >
            <label>
              任务类型
              <select
                value={mode}
                onChange={(e) =>
                  setForm((old) => ({
                    ...old,
                    mode: e.target.value as Execution["mode"],
                    ...(e.target.value === "replan"
                      ? { profile: "pipeline_eval", stop_at: "stage3" }
                      : {}),
                  }))
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
                onChange={(e) =>
                  setForm((old) => ({
                    ...old,
                    config_id: e.target.value,
                    scene_offsets: [],
                  }))
                }
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
            {["reference", "candidate", "replan"].includes(mode) && (
              <label>
                参考运行
                <select
                  required
                  value={form.reference_run_id ?? ""}
                  onChange={(e) =>
                    setForm((old) => ({
                      ...old,
                      reference_run_id: e.target.value,
                      scene_offsets: [],
                    }))
                  }
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
            {["reference", "candidate", "replan"].includes(mode) && (
              <ErrorNotice error={referenceRuns.error} />
            )}
            {mode === "replan" && (
              <div className="candidate-fields">
                <p className="small muted">
                  沿用源任务的视频、语义和相机，将已验收候选作为新初始布局。原生
                  Stage 3
                  将生成新控制并重新检查任务目标；旧轨迹不会复制。缺少来源凭据或候选版本变化时阻止启动。
                </p>
                {taskInputs.isFetching && <Loading />}
                <ErrorNotice error={taskInputs.error} />
                {taskInputs.data && (
                  <>
                    <p>
                      <strong>沿用任务</strong>
                      <br />
                      {taskInputs.data.task}
                    </p>
                    <p className="small muted">
                      输入版本{" "}
                      <code>{taskInputs.data.sha256.slice(0, 12)}</code>
                      <br />
                      候选{" "}
                      <code>
                        {taskInputs.data.candidate_sha256.slice(0, 12)}
                      </code>
                    </p>
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
                        <option value="stage3">新动作规划与验收</option>
                        <option value="stage4">规划通过后继续数据生成</option>
                      </select>
                    </label>
                    <button
                      type="button"
                      className="button"
                      disabled={taskInputs.isFetching}
                      onClick={() => taskInputs.refetch()}
                    >
                      重新读取任务输入
                    </button>
                    <p className="small muted">
                      external Agent
                      按本次运行独立响应；沿用完整原生门禁，布局通过不代表任务通过。
                    </p>
                  </>
                )}
              </div>
            )}
            {mode === "candidate" && (
              <div className="candidate-fields">
                <p className="small muted">
                  选择已打包 MJCF 场景的 DexAgent
                  运行。按世界坐标平移自由物体（米，Z 向上），复用 DexAgent
                  既有刚体执行模型。启动后保存独立候选并检查落稳；任务成功需后续动作规划与验收。
                </p>
                {candidateScene.isFetching && <Loading />}
                <ErrorNotice error={candidateScene.error} />
                {candidateScene.data && (
                  <>
                    <p className="small muted">
                      场景与资产版本{" "}
                      <code>{candidateScene.data.sha256.slice(0, 12)}</code>
                    </p>
                    {candidateScene.data.objects.map((object) => (
                      <fieldset key={object.name} className="candidate-object">
                        <legend>{object.name}</legend>
                        <p className="small muted">
                          原始位置{" "}
                          {object.position_m
                            .map((v) => v.toFixed(4))
                            .join(", ")}{" "}
                          m
                        </p>
                        <div className="candidate-offsets">
                          {(["X", "Y", "Z"] as const).map((axis, index) => (
                            <label key={axis}>
                              {axis} 平移（米）
                              <input
                                type="number"
                                step="0.001"
                                min="-1"
                                max="1"
                                required
                                aria-label={`${object.name} ${axis} 平移（米）`}
                                value={
                                  form.scene_offsets?.find(
                                    (value) => value.name === object.name,
                                  )?.translation_m[index] ?? 0
                                }
                                onChange={(e) => {
                                  const translation: [number, number, number] =
                                    [
                                      ...(form.scene_offsets?.find(
                                        (value) => value.name === object.name,
                                      )?.translation_m ?? [0, 0, 0]),
                                    ];
                                  translation[index] = e.target.valueAsNumber;
                                  patch("scene_offsets", [
                                    ...(form.scene_offsets ?? []).filter(
                                      (value) => value.name !== object.name,
                                    ),
                                    {
                                      name: object.name,
                                      translation_m: translation,
                                    },
                                  ]);
                                }}
                              />
                            </label>
                          ))}
                        </div>
                      </fieldset>
                    ))}
                    <button
                      type="button"
                      className="button"
                      disabled={candidateScene.isFetching}
                      onClick={() => candidateScene.refetch()}
                    >
                      重新读取场景版本
                    </button>
                  </>
                )}
              </div>
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
            {mode !== "pipeline" && mode !== "replan" && (
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
              disabled={
                busy ||
                active ||
                !catalog.data?.available ||
                !configId ||
                (mode === "candidate" &&
                  (!candidateScene.data ||
                    candidateScene.isFetching ||
                    !!candidateScene.error)) ||
                (mode === "replan" &&
                  (!taskInputs.data ||
                    taskInputs.isFetching ||
                    !!taskInputs.error))
              }
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
              {selected.mode === "candidate" && (
                <div className="notice execution-candidate-result">
                  <strong>场景候选物理验收</strong>
                  {selected.candidate_sha256 && (
                    <code className="execution-request-path">
                      {selected.candidate_sha256}
                    </code>
                  )}
                  <p>
                    此结果检查当前布局的落稳、漂移、旋转、速度与穿透。前往原生报告查看各物体指标和录制回放；任务成功与训练数据资格尚未评估。
                  </p>
                </div>
              )}
              {selected.mode === "replan" && (
                <div className="notice execution-candidate-result">
                  <strong>基于候选生成新动作</strong>
                  <code className="execution-request-path">
                    {selected.task_inputs_sha256}
                  </code>
                  <p>
                    输入版本包含已验收候选与源任务。旧轨迹不会复用；新的规划、物理和交付门禁通过前，任务成功尚未确认。
                  </p>
                </div>
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
                {selected.mode === "candidate" &&
                  selected.run_id &&
                  selected.status === "completed" &&
                  nativeRun.data?.validation === "passed" && (
                    <Link
                      className="button"
                      to={`/executions?mode=replan&source=${selected.run_id}`}
                      onClick={() =>
                        setForm((old) => ({
                          ...old,
                          mode: "replan",
                          reference_run_id: selected.run_id,
                          profile: "pipeline_eval",
                          stop_at: "stage3",
                        }))
                      }
                    >
                      基于候选重新规划
                    </Link>
                  )}
                {selected.mode === "candidate" && selected.run_id && (
                  <Link
                    className="button"
                    to={`/executions?mode=candidate&source=${selected.run_id}`}
                    onClick={() =>
                      setForm((old) => ({
                        ...old,
                        mode: "candidate",
                        reference_run_id: selected.run_id,
                        scene_offsets: [],
                      }))
                    }
                  >
                    从此候选继续调整
                  </Link>
                )}
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
