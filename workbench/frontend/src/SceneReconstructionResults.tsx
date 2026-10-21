import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "./api";
import type { Artifact, CleanupJob, RunDetail } from "./api";
import { Empty, ErrorNotice, Loading, formatBytes } from "./components";
import { statusNames, CleanupVideoComparison } from "./BackgroundCleanupPage";

const steps = [
  ["scene", "场景结果"],
  ["separation", "背景与对象分离"],
  ["background", "背景补全"],
  ["assets", "对象资产"],
];

export default function SceneReconstructionResults({ run, children, open }: {
  run: RunDetail; children: ReactNode; open: (artifact: Artifact) => void;
}) {
  const [params, setParams] = useSearchParams();
  const requested = params.get("sceneStep") ?? (params.has("cleanup") ? "separation" : "scene");
  const step = steps.some(([id]) => id === requested) ? requested : "scene";
  const selectedId = params.get("cleanup");
  const update = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    next.set(key, value);
    setParams(next);
  };
  const jobs = useQuery({
    queryKey: ["background-cleanup-jobs", run.id], queryFn: () => api.cleanupJobs(run.id),
    enabled: step === "separation" || step === "background", refetchInterval: 5_000,
  });
  const explicitJob = useQuery({
    queryKey: ["cleanup-job", selectedId], queryFn: () => api.cleanupJob(selectedId!),
    enabled: !!selectedId && (step === "separation" || step === "background"), refetchInterval: 5_000,
  });
  // A deep link is not an association. Never display another run's job as this run's result.
  const linkedJobs = jobs.data?.filter(item => item.run_id === run.id) ?? [];
  const job = selectedId
    ? explicitJob.data?.run_id === run.id ? explicitJob.data : undefined
    : linkedJobs[0];
  const choices = job && !linkedJobs.some(item => item.id === job.id)
    ? [job, ...linkedJobs] : linkedJobs;
  const assets = useQuery({
    queryKey: ["reconstruction-assets", run.id, run.stages.find(s => s.id === "stage2")?.report?.sha256],
    queryFn: () => api.artifacts(run.id, new URLSearchParams({ folder: "stage2/assets", kind: "model", limit: "200" }).toString()),
    enabled: step === "assets",
  });
  const reviewUrl = `/scenes/separation?${new URLSearchParams({ run: run.id, ...(job ? { job: job.id } : {}) })}`;
  return <section className="reconstruction-results" aria-label="场景重建子步骤">
    <div className="reconstruction-step-tabs" role="group" aria-label="重建结果类型">
      {steps.map(([id, title]) => <button key={id} aria-pressed={step === id} onClick={() => update("sceneStep", id)}>{title}</button>)}
    </div>
    {step === "scene" && children}
    {(step === "separation" || step === "background") && <>
      <div className="reconstruction-job-toolbar">
        <select aria-label="关联的分离任务" value={job?.id ?? ""} onChange={event => update("cleanup", event.target.value)}>
          {!job && <option value="">选择已关联任务</option>}
          {choices.map(item => <option key={item.id} value={item.id}>{item.source_video.name} · {statusNames[item.status]} · {item.id.slice(0, 8)}</option>)}
        </select>
        <Link className="button" to={reviewUrl}>打开分离与复核</Link>
      </div>
      <ErrorNotice error={jobs.error ?? explicitJob.error} />
      {(jobs.isLoading || explicitJob.isLoading) ? <Loading /> : job ? <CleanupResult job={job} step={step} /> :
        <Empty title={selectedId ? "此任务未关联到当前运行" : "尚无明确关联的分离任务"}
          detail="从分离页面创建或明确关联任务后，这里展示同一任务的 mask 与补全结果。不会按文件名自动配对。" />}
    </>}
    {step === "assets" && <>
      <p className="small muted reconstruction-caption">此处列出本次重建资产目录中的模型文件。背景移除完成不会自动赋予对象资产物理验收资格。</p>
      <ErrorNotice error={assets.error} />
      {assets.isLoading ? <Loading /> : assets.data?.items.length ? <div className="workflow-files">
        {assets.data.items.map(artifact => <button key={artifact.id} onClick={() => open(artifact)}>
          <span>{artifact.name}<small className="muted">{artifact.path}</small></span><small>{formatBytes(artifact.size)}</small>
        </button>)}
        {assets.data.total > 200 && <p className="small muted">显示前 200 / {assets.data.total} 项；其余文件可在产物页查看。</p>}
      </div> : !assets.error && <Empty title="尚未索引独立对象资产" detail="对象需要独立的几何、碰撞与物理属性；mask 或干净背景视频不能代替对象资产。" />}
    </>}
  </section>;
}

function CleanupResult({ job, step }: { job: CleanupJob; step: string }) {
  return <div className="reconstruction-cleanup-result">
    <div className="reconstruction-result-heading"><strong>{job.source_video.name}</strong><span className="source-tag">{statusNames[job.status]}</span></div>
    <p className="small muted">{job.capture_type === "scene_scan" ? "场景扫描" : job.capture_type === "operation_demo" ? "操作演示 · 清理流程测试" : "采集类型未确认"} · {job.propagation_model} · {job.current_step}</p>
    {(job.status === "failed" || job.status === "stopped") && <div className={`notice ${job.status === "failed" ? "danger" : ""}`}>{job.message} 以下保留产物仅用于检查，不表示本步骤完成。</div>}
    {step === "separation" && <>
      {job.detected_objects.length > 0 && <div className="cleanup-object-tags">{job.detected_objects.map(name => <span key={name}>{name}</span>)}</div>}
      {job.mask_summary && <p className="small muted">{job.mask_summary.segmentation_model} · {job.mask_summary.propagation_model} · {job.mask_summary.frame_count ?? "未记录"} 帧</p>}
      {job.mask_preview_url && <details className="reconstruction-mask-image"><summary>查看关键帧分割</summary><img src={job.mask_preview_url} alt="关联任务的关键帧对象分割" /><p className="small muted">关键帧分割；跨帧一致性需结合整段 mask 检查</p></details>}
      {job.mask_video_url ? <CleanupVideoComparison job={job} outputUrl={job.mask_video_url}
        outputLabel="移除区域 mask" testId="reconstruction-mask-pair" /> : !job.mask_preview_url && <Empty title="尚无分割预览" detail={job.message} />}
    </>}
    {step === "background" && (job.cleaned_video_url ? <>
      <CleanupVideoComparison job={job} outputUrl={job.cleaned_video_url}
        outputLabel="背景补全结果" testId="reconstruction-background-pair" />
      <p className="small muted">补全视频是重建输入；仍需检查几何、尺度、遮挡伪影及场景对齐。</p>
    </> : <Empty title="尚无背景补全结果" detail={job.status === "review_mask" ? "当前 mask 等待复核，确认后才会运行补洞。" : job.message} />)}
    {job.scene_path && <p className="small muted reconstruction-scene-path">生成的场景文件：{job.scene_path}</p>}
  </div>;
}
