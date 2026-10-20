import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, RefreshCw, TriangleAlert } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import { api } from "./api";
import type { Artifact } from "./api";
import {
  Badge,
  Empty,
  ErrorNotice,
  formatTime,
  Loading,
  pretty,
} from "./components";

type TrialDetail = Awaited<ReturnType<typeof api.planningTrial>>;

function record(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

function finiteNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value)
    ? value
    : undefined;
}

function peak(values: Array<number | undefined>): number | undefined {
  const present = values.filter(
    (value): value is number => value !== undefined,
  );
  return present.length
    ? present.reduce((max, value) => Math.max(max, value))
    : undefined;
}

function formatNumber(value: number | undefined, unit: string): string {
  return value === undefined
    ? "未记录"
    : `${value.toLocaleString("zh-CN", { maximumFractionDigits: 3 })} ${unit}`;
}

function relativeReportPath(path: string): string {
  const parts = path.split(/[\\/]+/).filter(Boolean);
  const stage3 = parts.lastIndexOf("stage3");
  return stage3 >= 0
    ? parts.slice(stage3).join("/")
    : parts.slice(-3).join("/");
}

function Metric({
  label,
  value,
  source,
}: {
  label: string;
  value: string;
  source: string;
}) {
  return (
    <div className="plan-metric">
      <span>{label}</span>
      <strong>{value}</strong>
      <code>{source}</code>
    </div>
  );
}

function Step({ step }: { step: TrialDetail["subgoals"][number] }) {
  const measured = step.measured;
  const settle = record(measured.release_settle);
  const settleIssues = Array.isArray(settle?.issues) ? settle.issues : [];
  const hasSettle = settle !== undefined;
  const frameRange = step.frames
    ? `${step.frames[0]}–${step.frames[1]}`
    : "未记录";
  const stepStatus = step.ok
    ? step.failed.length > 0 || !!step.error
      ? "incomplete"
      : "passed"
    : "failed";

  return (
    <details
      className={`plan-step ${stepStatus}`}
      open={!step.ok || step.failed.length > 0 || !!step.error}
      key={`${step.index}:${step.motion}`}
    >
      <summary>
        <strong>
          #{String(step.index).padStart(2, "0")} {step.motion}
        </strong>
        <span className="plan-tag">手臂 {step.side || "未记录"}</span>
        <span className="plan-tag">对象 {step.object || "未记录"}</span>
        <Badge value={stepStatus} />
        <span className="plan-frame">全局帧范围：{frameRange}</span>
      </summary>
      <div className="plan-step-body">
        {step.failed.length > 0 && (
          <div className="plan-failures">
            <h4>失败标识</h4>
            <div className="plan-identifiers">
              {step.failed.map((failure, index) => (
                <code key={`${failure}:${index}`}>{failure}</code>
              ))}
            </div>
          </div>
        )}
        {step.error && (
          <div className="notice danger plan-native-error">
            <TriangleAlert size={16} />
            <div>
              <strong>原生错误文本</strong>
              <pre>{step.error}</pre>
            </div>
          </div>
        )}
        {hasSettle && (
          <div
            className={`plan-settle ${settle?.ok === false ? "failed" : ""}`}
          >
            <strong>
              释放后静置检查
              {settle?.ok === true
                ? " · 原生标记通过"
                : settle?.ok === false
                  ? " · 原生标记失败"
                  : " · 有记录"}
            </strong>
            {settleIssues.length > 0 ? (
              <div className="plan-identifiers">
                {settleIssues.map((issue, index) => (
                  <code key={index}>
                    {typeof issue === "string" ? issue : pretty(issue)}
                  </code>
                ))}
              </div>
            ) : (
              <p className="small muted">没有记录静置问题标识。</p>
            )}
          </div>
        )}
        <details className="plan-measured">
          <summary>原始 measured 数据</summary>
          <pre>{pretty(measured)}</pre>
        </details>
      </div>
    </details>
  );
}

export default function PlanningDiagnostics({
  runId,
  open,
}: {
  runId: string;
  open: (artifact: Artifact) => void;
}) {
  const [params, setParams] = useSearchParams();
  const trialsQuery = useQuery({
    queryKey: ["planning-trials", runId],
    queryFn: () => api.planningTrials(runId),
    refetchInterval: 5_000,
  });
  const items = trialsQuery.data?.items ?? [];
  const requestedId = params.get("trial");
  const selected =
    items.find((trial) => trial.report.id === requestedId) ?? items[0];
  const detailQuery = useQuery({
    queryKey: ["planning-trial", runId, selected?.report.id, selected?.sha256],
    queryFn: () => api.planningTrial(runId, selected!.report.id),
    enabled: !!selected,
    refetchInterval: selected ? 5_000 : false,
  });

  useEffect(() => {
    if (!trialsQuery.isSuccess) return;
    const nextId = selected?.report.id;
    if (nextId === (params.get("trial") ?? undefined)) return;
    const next = new URLSearchParams(params);
    if (nextId) next.set("trial", nextId);
    else next.delete("trial");
    setParams(next, { replace: true });
  }, [params, selected?.report.id, setParams, trialsQuery.isSuccess]);

  const detail =
    detailQuery.data?.sha256 === selected?.sha256
      ? detailQuery.data
      : undefined;
  const shown = detail ?? selected;
  const steps = detail?.subgoals ?? [];
  const handPenetration = peak(
    steps.map((step) => finiteNumber(step.measured.max_hand_pen_mm)),
  );
  const targetRotationError = peak(
    steps.map((step) => finiteNumber(step.measured.target_rotation_error_deg)),
  );
  const releaseMovementMm = peak(
    steps.map((step) => {
      const settle = record(step.measured.release_settle);
      const movementM = finiteNumber(settle?.moved_since_release_m);
      return movementM === undefined ? undefined : movementM * 1000;
    }),
  );

  function selectTrial(id: string) {
    const next = new URLSearchParams(params);
    if (id) next.set("trial", id);
    else next.delete("trial");
    setParams(next);
  }

  return (
    <section className="planning-diagnostics">
      <div className="plan-heading">
        <div>
          <p className="eyebrow">NATIVE STAGE 3 / DIAGNOSTICS</p>
          <h2>规划诊断</h2>
        </div>
        <button
          className="button"
          onClick={() => void trialsQuery.refetch()}
          disabled={trialsQuery.isFetching}
        >
          <RefreshCw size={14} /> 刷新
        </button>
      </div>

      <ErrorNotice error={trialsQuery.error} />
      {trialsQuery.data?.truncated && (
        <div className="notice" role="status">
          规划报告目录扫描已截断；当前列表可能未包含所有报告。
        </div>
      )}
      {trialsQuery.data?.issues.map((issue, index) => (
        <details
          className="notice danger plan-issue"
          key={`${issue.path}:${index}`}
        >
          <summary>报告读取问题 · {issue.path}</summary>
          <p>{issue.message}</p>
        </details>
      ))}

      {trialsQuery.isPending ? (
        <Loading />
      ) : items.length === 0 ? (
        !trialsQuery.error && (
          <Empty
            title="没有可读取的规划报告"
            detail="本页读取 Stage 3 原生 execution_report.json。"
          />
        )
      ) : (
        <>
          <label className="plan-selector">
            <span>选择规划试验</span>
            <select
              aria-label="选择规划试验"
              value={selected?.report.id ?? ""}
              onChange={(event) => selectTrial(event.target.value)}
            >
              {items.map((trial) => (
                <option key={trial.report.id} value={trial.report.id}>
                  {relativeReportPath(trial.report.path)} ·{" "}
                  {formatTime(trial.report.modified)}
                </option>
              ))}
            </select>
          </label>

          {selected && (
            <>
              <div className="plan-report-head">
                <div className="plan-report-title">
                  <div className="plan-outcome">
                    <Badge value={shown?.outcome ?? selected.outcome} />
                    <code>试验结果：{shown?.outcome ?? selected.outcome}</code>
                  </div>
                  <code className="plan-path">
                    {relativeReportPath(selected.report.path)}
                  </code>
                </div>
                <button
                  className="link-button"
                  onClick={() => open(selected.report)}
                >
                  原始报告 <ArrowUpRight size={14} />
                </button>
              </div>

              {(shown?.dry_run ?? selected.dry_run) ? (
                <div className="plan-scope dry-run" role="status">
                  <TriangleAlert size={18} />
                  <div>
                    <strong>仅可达性预检 / 未运行物理</strong>
                    <p>该结果只表示规划预检结果，不代表物理执行结果。</p>
                  </div>
                </div>
              ) : (
                <div className="plan-scope" role="note">
                  <TriangleAlert size={18} />
                  <div>
                    <strong>物理试验仅用于诊断</strong>
                    <p>
                      该物理试验仅用于诊断；结果不能作为正式成功结论或合格执行记录。
                    </p>
                  </div>
                </div>
              )}

              <div className="plan-metrics" aria-label="规划试验摘要指标">
                <Metric
                  label="最大手部穿透"
                  value={formatNumber(handPenetration, "mm")}
                  source="max_hand_pen_mm · 记录峰值"
                />
                <Metric
                  label="目标旋转误差"
                  value={formatNumber(targetRotationError, "deg")}
                  source="target_rotation_error_deg · 记录峰值"
                />
                <Metric
                  label="释放后移动"
                  value={formatNumber(releaseMovementMm, "mm")}
                  source="moved_since_release_m × 1000 · 记录峰值"
                />
                <Metric
                  label="全局帧数"
                  value={
                    shown?.n_frames == null ? "未记录" : `${shown.n_frames} 帧`
                  }
                  source="n_frames"
                />
                <Metric
                  label="报告时长"
                  value={formatNumber(shown?.duration_s ?? undefined, "s")}
                  source="duration_s"
                />
                <Metric
                  label="子目标记录"
                  value={`${shown?.recorded_subgoals ?? selected.recorded_subgoals} / ${shown?.expected_subgoals ?? selected.expected_subgoals}`}
                  source={`已通过 ${shown?.passed_subgoals ?? selected.passed_subgoals} 个子目标`}
                />
              </div>

              {(shown?.failed_checks ?? selected.failed_checks).length > 0 && (
                <section className="plan-failures plan-overall-failures">
                  <h3>失败标识</h3>
                  <div className="plan-identifiers">
                    {(shown?.failed_checks ?? selected.failed_checks).map(
                      (failure, index) => (
                        <code key={`${failure}:${index}`}>{failure}</code>
                      ),
                    )}
                  </div>
                </section>
              )}

              <ErrorNotice error={detailQuery.error} />
              {detailQuery.isPending ? (
                <Loading />
              ) : detail ? (
                <div className="plan-steps">
                  <div className="plan-section-heading">
                    <div>
                      <h3>逐子目标记录</h3>
                      <p className="small muted">
                        仅 step.frames 是全局帧范围；measured 中的
                        release_settle.release_frame 和 settle_frames
                        属于子目标局部帧。
                      </p>
                    </div>
                    <span className="small muted">
                      {detail.subgoals.length} 条
                    </span>
                  </div>
                  {detail.subgoals.length === 0 ? (
                    <Empty title="报告没有子目标明细" />
                  ) : (
                    detail.subgoals.map((step) => (
                      <Step key={`${step.index}:${step.motion}`} step={step} />
                    ))
                  )}
                </div>
              ) : null}
            </>
          )}
        </>
      )}
    </section>
  );
}
