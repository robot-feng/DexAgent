import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ArrowUpRight, FileCheck2, FolderOpen } from "lucide-react";
import { api } from "./api";
import type { Artifact, RunDetail, Stage } from "./api";
import { Badge, Empty, ErrorNotice, GateReport, Loading, Media } from "./components";
import { EpisodeList } from "./pages";
import SceneReconstructionResults from "./SceneReconstructionResults";

// A projection of existing evidence, not a second pipeline or execution registry.
const phases = [
  { id: "input", title: "输入与校验", stage: "preflight", folder: "reports/preflight.json", input: "场景采集 / 操作示范 / 指令 / 机器人数据", output: "原始输入、输入契约与环境检查", check: "输入可读性、标定、时间与坐标约定", contract: "现有 input_contract 与 preflight；场景和示范可以独立导入。", route: "/executions", action: "配置任务输入" },
  { id: "scene", title: "场景与资产重建", stage: "stage2", folder: "stage2/", input: "场景采集或已有场景资产", output: "场景、对象模型、相机与重建对照", check: "尺度、几何、背景分离、对象与相机对齐", contract: "现有场景资产 → RSS Scene / Binding；分割和补洞通过背景清理配置切换。", route: "/scenes/separation", action: "背景与对象分离" },
  { id: "task", title: "任务理解", stage: "stage1", folder: "stage1/", input: "任务指令 + 场景；操作示范可选", output: "对象关系、子目标和成功条件", check: "对象身份、阶段关系、目标与成功谓词", contract: "复用 scene_semantics 与任务 gates，任务目标不随动作生成器改变。", route: "/tools", action: "查看任务工具" },
  { id: "reference", title: "轨迹提取", stage: "reference", folder: "reference/", input: "操作视频或已有动作记录（可选）", output: "人体 / 手 / 对象参考轨迹与时间映射", check: "相机运动、尺度、坐标和示范到场景的配准", contract: "原始参考与机器人动作分开；提取适配器通过 reference/stage_report.json 记录自己的结果与检查。", route: "/tools", action: "查看提取与导入工具" },
  { id: "motion", title: "重定向与动作生成", stage: "stage3", folder: "stage3/", input: "任务 + 场景 + 机器人；参考轨迹可选", output: "机器人轨迹、动作清单和规划诊断", check: "关节映射、可达性、碰撞、接触和动作限幅", contract: "重定向、规划和策略是不同动作来源；复用 trajectory_manifest 与现有执行入口。", route: "/executions", action: "配置动作执行" },
  { id: "execution", title: "物理执行与验证", stage: "execution", folder: "execution/", input: "目标机器人场景 + 动作 / 策略", output: "实际执行记录、视频、失败原因与任务验收", check: "真实目标后端的动力学与任务条件；回放不等于任务通过", contract: "复用 RSS / 原生后端报告和 Episode。每个本体保留自己的控制语义与证据。", route: "/executions", action: "查看执行任务" },
  { id: "data", title: "数据生成与扩增", stage: "stage4", folder: "stage3/stage4_task/outputs/", input: "通过任务验收的执行记录", output: "Episode、随机化结果、渲染与导出报告", check: "任务成功、数据完整性、随机化后重新求解和验收", contract: "复用 Episode / bundle；导出成功与训练资格分别判断。", route: "/datasets", action: "浏览数据集" },
  { id: "training", title: "策略训练", stage: "training", folder: "training/", input: "合格数据集版本 + 策略配置", output: "checkpoint、训练曲线、数据与配置身份", check: "训练配置、数据版本、指标和可恢复状态", contract: "训练适配器通过 training/stage_report.json 接入结果；FlashCHORD / HumanEgo 尚未连接为训练执行器。", route: "", action: "" },
  { id: "evaluation", title: "闭环评估与真机反馈", stage: "evaluation", folder: "evaluation/", input: "策略 checkpoint + 评估任务与机器人", output: "闭环 Episode、成功率和仿真修正证据", check: "策略实际执行、任务谓词、仿真与真机差异", contract: "评估适配器通过 evaluation/stage_report.json 接入结果，需明确仿真或真机范围；不能由演示视频推断成功。", route: "", action: "" },
];
type Phase = typeof phases[number];

function boundStage(run: RunDetail, phase: Phase): Stage | undefined {
  const explicit = run.stages.find(s => s.id === phase.stage);
  if (explicit) return explicit;
  if (phase.id !== "execution") return undefined;
  const backend = run.stages.find(s => s.id === "backend");
  if (backend) return backend;
  // DexAgent records trajectory generation and its physical execution in one
  // native Stage 3 report. Surface the embedded execution gate here as well.
  const motion = run.stages.find(s => s.id === "stage3");
  const gate = motion?.report?.checks.find(check => check.id === "stage3_gate_suite");
  const evidence = gate?.observed as Record<string, unknown> | undefined;
  const checks = evidence?.report as Record<string, unknown> | undefined;
  return typeof checks?.episode_physics_check === "boolean" &&
    typeof checks?.stage1_goal_gate === "boolean" ? motion : undefined;
}

function selectedPhase(run: RunDetail, phaseId: string | null): Phase {
  return phases.find(p => p.id === phaseId) ??
    (run.stages.some(s => s.id === "backend" || s.id === "execution") ? phases[5] : phases[0]);
}

export function workflowStage(run: RunDetail, phaseId: string | null): Stage | undefined {
  return boundStage(run, selectedPhase(run, phaseId));
}

export default function WorkflowView({ run, phaseId, choose, open, inspect }: {
  run: RunDetail; phaseId: string | null; choose: (id: string, stage: string) => void;
  open: (artifact: Artifact) => void; inspect: (stage: string) => void;
}) {
  const phase = selectedPhase(run, phaseId);
  return <section className="workflow" aria-label="完整流程工作区">
    <div className="workflow-intro">
      <div><p className="eyebrow">EMBODIFORGE / WORKFLOW</p><h2>从真实输入到可验证的数据</h2></div>
      <p>场景与示范独立输入 · 按环节查看结果与证据</p>
    </div>
    <nav className="workflow-track" aria-label="流程环节">
      {phases.map((p, i) => {
        const stage = boundStage(run, p);
        return <button key={p.id} aria-current={p.id === phase.id ? "step" : undefined}
          onClick={() => choose(p.id, boundStage(run, p)?.id ?? p.stage)} className={p.id === phase.id ? "selected" : ""}>
          <span className="workflow-index">{String(i + 1).padStart(2, "0")}</span>
          <strong>{p.title}</strong>
          {stage?.report ? <Badge value={stage.report.validation} /> :
            <small>{stage?.progress ? "有进度记录" : ["reference", "training", "evaluation"].includes(p.id) ? "未记录" : "无阶段报告"}</small>}
        </button>;
      })}
    </nav>
    <PhaseResult key={`${run.id}:${phase.id}:${boundStage(run, phase)?.id ?? "none"}`} run={run} phase={phase} open={open} inspect={inspect} />
  </section>;
}

function PhaseResult({ run, phase, open, inspect }: {
  run: RunDetail; phase: Phase; open: (a: Artifact) => void; inspect: (stage: string) => void;
}) {
  const stage = boundStage(run, phase);
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [panel, setPanel] = useState("result");
  // Query only the selected stage. Paging avoids scanning/downloading every image in a run.
  const folder = phase.id === "execution" && stage?.id === "backend" ? "" :
    phase.id === "execution" && stage?.id === "stage3" ? "stage3/" : phase.folder;
  const files = useQuery({
    queryKey: ["workflow-artifacts", run.id, phase.id, folder, stage?.report?.sha256, offset],
    queryFn: () => api.artifacts(run.id, new URLSearchParams({ folder, offset: String(offset), limit: "200" }).toString()),
    refetchInterval: run.execution === "running" || stage?.report?.source_status === "running" || !!stage?.progress ? 10_000 : false,
  });
  const mediaFiles = useQuery({
    queryKey: ["workflow-media", run.id, phase.id, folder, stage?.report?.sha256],
    queryFn: async () => {
      const pages = await Promise.all(["video", "image"].map(kind => api.artifacts(run.id,
        new URLSearchParams({ folder, kind, limit: "200" }).toString())));
      return { items: pages.flatMap(page => page.items), truncated: pages.some(page => page.total > 200) };
    },
    enabled: phase.id !== "input",
    refetchInterval: run.execution === "running" || stage?.report?.source_status === "running" || !!stage?.progress ? 10_000 : false,
  });
  const items = (files.data?.items ?? []).filter(a => !(phase.id === "execution" && stage?.id === "stage3"
    && /\/stage4_task\//.test(a.path)));
  // Never silently choose a historical attempt or a downstream replay as a current-stage preview.
  const current = (mediaFiles.data?.items ?? []).filter(a => !/(?:\/reports\/|\/previous-|\/\.candidates\/|\/\.recoveries\/|\/history\/|\/attempts\/)/.test(a.path)
    && !(["motion", "execution"].includes(phase.id) && /\/stage4_task\//.test(a.path)));
  const visuals = current.filter(a => a.kind === "image" || a.kind === "video");
  const options = phase.id === "input" ? (run.input_video ? [run.input_video] : []) : visuals.filter(a => a.id !== run.input_video?.id);
  const media = options.find(a => a.id === selected) ?? options.find(a => ["frame0_compare.png", "sim_video.mp4", "replay_mechanism.mp4", "contact_sheet.jpg"].includes(a.name)) ?? options.find(a => a.kind === "video") ?? options[0];
  const result = (<>
          {phase.id === "task" && run.task && <p className="workflow-task">{run.task}</p>}
          {media ? <><div className="workflow-preview"><Media artifact={media} key={media.id} /></div>
            <div className="workflow-preview-footer"><select aria-label="当前环节预览" value={media.id} onChange={e => setSelected(e.target.value)}>
              {options.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
            </select><button className="link-button" onClick={() => open(media)}>展开 <ArrowUpRight size={14} /></button></div>
            <p className="small muted workflow-path">{media.path}</p></> : mediaFiles.isLoading ? <Loading /> :
            <Empty title={files.error || mediaFiles.error ? "结果暂不可读取" : "此环节暂无可预览结果"}
              detail={phase.id === "reference" ? "演示参考是可选输入；已有轨迹仍可从产物页检查，没有独立报告时不推断已完成提取。" : "查看验收证据和原生文件；没有结果不代表通过，也不代表该任务必须执行此环节。"} />}
          {mediaFiles.data?.truncated && <p className="small muted">预览列表显示每种媒体的前 200 项，其他结果请在文件分页中查看。</p>}
          {phase.id === "data" && <EpisodeList runId={run.id} />}
        </>);
  const displayedReport = phase.id === "execution" && stage?.id === "stage3" && stage.report
    ? { ...stage.report, checks: stage.report.checks.filter(check => check.id === "stage3_gate_suite") }
    : stage?.report;
  return <div className="workflow-detail">
    <header className="workflow-detail-head"><div><p className="eyebrow">当前环节</p><h2>{phase.title}</h2></div>
      {stage?.report ? <Badge value={stage.report.validation} /> : <span className="source-tag">尚无独立验收证据</span>}
    </header>
    <div className="workflow-io">
      <div><small>输入</small><p>{phase.input}</p></div><span aria-hidden="true">→</span><div><small>输出</small><p>{phase.output}</p></div>
    </div>
    <div className="workflow-body">
      <div className="workflow-result">
        <div className="workflow-switch" role="group" aria-label="环节内容">
          {[['result', '结果预览'], ['checks', '验收证据'], ['files', '输入与产物文件'], ['provenance', '来源与配置']].map(([id, title]) =>
            <button key={id} aria-pressed={panel === id} onClick={() => setPanel(id)}>{title}</button>)}
        </div>
        <ErrorNotice error={files.error ?? mediaFiles.error} />
        {panel === "result" && (phase.id === "scene"
          ? <SceneReconstructionResults run={run} open={open}>{result}</SceneReconstructionResults>
          : result)}
        {panel === "checks" && (displayedReport ? <>
          {phase.id === "execution" && stage?.id === "stage3" &&
            <p className="small muted">物理执行与任务目标检查记录在 Stage 3 原生报告中；以下展示其中的执行门禁，原报告可直接打开。</p>}
          <GateReport report={displayedReport} open={open} />
        </> :
          <Empty title="未记录独立验收" detail={phase.check} />)}
        {panel === "provenance" && (displayedReport ? <ReportProvenance report={displayedReport} /> :
          <Empty title="本环节没有来源报告" detail="暂无可读取的输入、模型或配置身份。" />)}
        {panel === "files" && <>
          {files.isLoading ? <Loading /> : <div className="workflow-files">
            {phase.id === "input" && run.input_video && <button onClick={() => open(run.input_video!)}><FolderOpen size={16} /><span>{run.input_video.path}</span></button>}
            {items.map(a => <button key={a.id} onClick={() => open(a)}><FolderOpen size={16} /><span>{a.path}</span></button>)}
            {!items.length && <Empty title="未索引该环节文件" detail="所有已收录文件仍可通过产物页访问。" />}
          </div>}
        </>}
        {files.data && files.data.total > 200 && <div className="workflow-paging">
          <span className="small muted">文件 {offset + 1}–{Math.min(offset + 200, files.data.total)} / {files.data.total}</span>
          <button className="button" disabled={!offset} onClick={() => { setOffset(offset - 200); setSelected(null); }}>上一页</button>
          <button className="button" disabled={offset + 200 >= files.data.total} onClick={() => { setOffset(offset + 200); setSelected(null); }}>下一页</button>
        </div>}
      </div>
      <aside className="workflow-evidence">
        <FileCheck2 size={22} /><h3>验证什么</h3><p>{phase.check}</p>
        {displayedReport && stage && <><div className="workflow-check-count"><b>{displayedReport.checks.filter(c => c.status === "failed").length}</b><span>失败检查 / {displayedReport.checks.length} 项</span></div>
          {displayedReport.checks.some(check => check.required && !["passed", "warning"].includes(check.status)) &&
            <p className="small state-bad">未通过的必需检查：{displayedReport.checks.filter(check => check.required && !["passed", "warning"].includes(check.status)).length}</p>}
          <p className="small">报告：{phase.id === "execution" && stage?.id === "stage3" ? "Stage 3 物理执行门禁" : `${stage.title}（${stage.id}）`}<br />证据范围：{displayedReport.evaluation_scope || "源报告未声明"}<br />任务成功已评估：{displayedReport.task_success_evaluated === true ? "是" : "未确认"}</p>
          <button className="button full" onClick={() => setPanel("checks")}>查看本环节验收</button>
          <button className="link-button" onClick={() => inspect(stage.id)}>历史尝试与完整报告 <ArrowUpRight size={13} /></button></>}
        <hr /><h3>模块接口</h3><p>{phase.contract}</p>
        {phase.route && <Link className="button full" to={phase.id === "scene" ? `${phase.route}?run=${run.id}` : phase.route}>{phase.action} <ArrowUpRight size={13} /></Link>}
        <p className="small muted">此处展示当前运行已收录的证据；更换模型或后端后，需要重新执行相应检查。</p>
      </aside>
    </div>
  </div>;
}

function ReportProvenance({ report }: { report: NonNullable<Stage["report"]> }) {
  const inputs = Object.keys(report.inputs).length ? report.inputs : null;
  const models = Object.keys(report.model_identity).length ? report.model_identity : null;
  return <div className="workflow-provenance">
    <p className="small muted">以下内容来自本阶段原生报告，表示运行时记录的来源与产物声明。</p>
    <section><h3>输入</h3>{inputs ? <MetadataValue value={inputs} /> : <p className="small muted">未记录输入来源</p>}</section>
    <section><h3>模型与后端</h3>{models ? <MetadataValue value={models} /> : <p className="small muted">未记录模型身份</p>}</section>
    <section><h3>配置身份</h3><code>{report.config_sha256 || "未记录有效配置摘要"}</code></section>
    <section><h3>产物声明</h3>{report.declared_artifacts.length
      ? <ul>{report.declared_artifacts.map((item, index) => <li key={`${index}:${item}`}><code>{item}</code></li>)}</ul>
      : <p className="small muted">未记录产物声明；可在“输入与产物文件”查看本次运行收录的文件。</p>}</section>
    {!!report.provenance_issues.length && <div className="notice">来源元数据问题：{report.provenance_issues.join("；")}</div>}
  </div>;
}

function MetadataValue({ value, depth = 0 }: { value: unknown; depth?: number }) {
  if (value === null || value === undefined) return <span className="muted">—</span>;
  if (typeof value !== "object") return <code>{String(value)}</code>;
  if (Array.isArray(value)) {
    return <details className="workflow-metadata-details">
      <summary>{value.length} 项</summary>
      <ul>{value.slice(0, 12).map((item, index) => <li key={index}><MetadataValue value={item} depth={depth + 1} /></li>)}</ul>
      {value.length > 12 && <span className="small muted">另有 {value.length - 12} 项，完整内容见原生报告。</span>}
    </details>;
  }
  const entries = Object.entries(value as Record<string, unknown>);
  const fields = <div className="workflow-metadata">
    {entries.slice(0, 12).map(([key, item]) => <div className="workflow-metadata-row" key={key}>
      <strong>{key}</strong><MetadataValue value={item} depth={depth + 1} />
    </div>)}
    {entries.length > 12 && <details className="workflow-metadata-details">
      <summary>另有 {entries.length - 12} 个字段</summary>
      <MetadataValue value={Object.fromEntries(entries.slice(12))} depth={depth + 1} />
    </details>}
  </div>;
  return depth >= 2
    ? <details className="workflow-metadata-details"><summary>{entries.length} 个字段</summary>{fields}</details>
    : fields;
}
