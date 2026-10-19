import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Check, Film, Play, ShieldCheck, Square, WandSparkles } from "lucide-react";
import { api } from "./api";
import type { CleanupJob } from "./api";
import { ErrorNotice, Loading, formatBytes } from "./components";

const statusNames: Record<CleanupJob["status"], string> = {
  preparing: "准备与分割中",
  review_mask: "待检查 mask",
  inpainting: "视频补洞中",
  review_clean: "待检查清理结果",
  building: "3D 场景构建中",
  ready: "完成",
  failed: "失败",
  stopped: "已停止",
};

const isActive = (job: CleanupJob) =>
  ["preparing", "inpainting", "building"].includes(job.status);

function objectNames(value: string) {
  return value
    .split(/[,，\n]/)
    .map((name) => name.trim())
    .filter(Boolean);
}

export function BackgroundCleanupPage() {
  const client = useQueryClient();
  const catalog = useQuery({
    queryKey: ["background-cleanup-catalog"],
    queryFn: api.cleanupCatalog,
    refetchInterval: 30_000,
  });
  const jobs = useQuery({
    queryKey: ["background-cleanup-jobs"],
    queryFn: api.cleanupJobs,
    refetchInterval: (query) =>
      query.state.data?.some(isActive) ? 2_000 : 15_000,
  });
  const [videoId, setVideoId] = useState("");
  const [captureType, setCaptureType] = useState<"scene_scan" | "operation_demo" | "unknown">("unknown");
  const [floorCategory, setFloorCategory] = useState("desk");
  const [includeText, setIncludeText] = useState("robot arm, gripper");
  const [skipText, setSkipText] = useState("");
  const [useGemini, setUseGemini] = useState(false);
  const [propagationModel, setPropagationModel] = useState<"sam3" | "sam2">("sam3");
  const [selectedJobId, setSelectedJobId] = useState("");

  const selectedVideo = useMemo(
    () => catalog.data?.videos.find((video) => video.id === videoId),
    [catalog.data?.videos, videoId],
  );
  const selectedJob = jobs.data?.find((job) => job.id === selectedJobId) ?? jobs.data?.[0];
  const requirement = (id: string) => catalog.data?.requirements.find((item) => item.id === id);
  const canStart = Boolean(
    catalog.data?.preview_available &&
    (propagationModel === "sam3" || requirement("sam2_checkpoint")?.available) &&
    (!useGemini || requirement("gemini")?.available) &&
    videoId &&
    (useGemini || objectNames(includeText).length),
  );

  useEffect(() => {
    const videos = catalog.data?.videos;
    if (!videoId && videos?.length) {
      const preferred = videos.find((video) => video.name === "ThrowAwayTrash.mp4") ??
        videos.find((video) => video.capture_type === "scene_scan") ?? videos[0];
      setVideoId(preferred.id);
      setCaptureType(preferred.capture_type);
    }
  }, [catalog.data?.videos, videoId]);

  const refresh = () => {
    void client.invalidateQueries({ queryKey: ["background-cleanup-jobs"] });
    void client.invalidateQueries({ queryKey: ["background-cleanup-catalog"] });
  };
  const create = useMutation({
    mutationFn: () => api.createCleanup({
      video_id: videoId,
      capture_type: captureType,
      floor_category: floorCategory,
      include_objects: objectNames(includeText),
      skip_objects: objectNames(skipText),
      propagation_model: propagationModel,
      use_gemini_proposal: useGemini,
    }),
    onSuccess: (job) => {
      setSelectedJobId(job.id);
      refresh();
    },
  });
  const approve = useMutation({
    mutationFn: (id: string) => api.approveCleanupMask(id),
    onSuccess: (job) => { setSelectedJobId(job.id); refresh(); },
  });
  const build = useMutation({
    mutationFn: (id: string) => api.buildCleanupScene(id),
    onSuccess: (job) => { setSelectedJobId(job.id); refresh(); },
  });
  const stop = useMutation({
    mutationFn: (id: string) => api.stopCleanup(id),
    onSuccess: (job) => { setSelectedJobId(job.id); refresh(); },
  });

  return (
    <main className="page cleanup-page">
      <div className="page-title">
        <div>
          <p className="eyebrow">SIMFOUNDRY / BACKGROUND CLEANUP</p>
          <h1>背景清理</h1>
          <p className="muted">先检查对象 mask，再运行视频补洞；扫描视频通过复核后才进入 3D 场景构建。</p>
        </div>
        <span className="source-tag"><Film size={14} /> 本机视频</span>
      </div>

      {catalog.isLoading ? <Loading /> : null}
      <ErrorNotice error={catalog.error ?? jobs.error ?? create.error ?? approve.error ?? build.error ?? stop.error} />

      <section className="cleanup-layout">
        <div className="panel cleanup-form-panel">
          <div className="section-heading">
            <div>
              <h2>新建清理任务</h2>
              <p className="muted small">使用 SimFoundry Stage 1b、可选视频分割模型和 VOID；不会改写原始视频。</p>
            </div>
            <WandSparkles size={18} className="muted" />
          </div>
          <form className="cleanup-form" onSubmit={(event) => { event.preventDefault(); create.mutate(); }}>
            <label>
              <span>输入视频</span>
              <select aria-label="输入视频" value={videoId} onChange={(event) => {
                const video = catalog.data?.videos.find((item) => item.id === event.target.value);
                setVideoId(event.target.value);
                if (video) setCaptureType(video.capture_type);
              }}>
                <option value="">选择本机视频…</option>
                {catalog.data?.videos.map((video) => (
                  <option key={video.id} value={video.id}>
                    {video.name} · {video.capture_type === "scene_scan" ? "相机扫描" : video.capture_type === "operation_demo" ? "操作演示" : "未分类"}
                  </option>
                ))}
              </select>
              {selectedVideo && <small className="mono muted">{selectedVideo.path} · {formatBytes(selectedVideo.size)}</small>}
            </label>
            {selectedVideo?.capture_type === "operation_demo" && (
              <div className="notice warning cleanup-warning">
                <AlertTriangle size={16} />
                <span>操作演示只用于 mask/补洞冒烟测试，固定机位没有相机视差，不能构建 3D 场景。</span>
              </div>
            )}
            <div className="cleanup-form-row">
              <label>
                <span>支撑面</span>
                <input value={floorCategory} onChange={(event) => setFloorCategory(event.target.value)} maxLength={120} required />
              </label>
              <label>
                <span>采集类型</span>
                <select aria-label="采集类型" value={captureType} onChange={(event) => setCaptureType(event.target.value as typeof captureType)}>
                  <option value="scene_scan" disabled={selectedVideo?.capture_type === "operation_demo"}>相机扫描</option>
                  <option value="operation_demo">操作演示</option>
                  <option value="unknown">未确认</option>
                </select>
              </label>
            </div>
            <label>
              <span>要移除的对象提示词</span>
              <textarea aria-label="要移除的对象提示词" value={includeText} onChange={(event) => setIncludeText(event.target.value)} placeholder="例如：robot arm, gripper" rows={3} />
              <small className="muted">逗号分隔。固定安装的机器人不一定会被自动提案发现，建议明确填写 robot arm、gripper 等。</small>
            </label>
            <label>
              <span>保留对象（可选）</span>
              <input aria-label="保留对象" value={skipText} onChange={(event) => setSkipText(event.target.value)} placeholder="逗号分隔" />
            </label>
            <div className="cleanup-model-row">
              <label>
                <span>视频分割模型</span>
                <select aria-label="视频分割模型" value={propagationModel} onChange={(event) => setPropagationModel(event.target.value as typeof propagationModel)}>
                  <option value="sam3">SAM3 单模型（文字分割 + 跨帧跟踪）</option>
                  <option value="sam2">SimFoundry 原流程（SAM3 mask + SAM2 传播）</option>
                </select>
              </label>
              <label className="check-row">
                <input type="checkbox" checked={useGemini} onChange={(event) => setUseGemini(event.target.checked)} />
                <span>同时使用 Gemini 提议桌面物体</span>
              </label>
            </div>
            <div className="notice cleanup-note">
              <ShieldCheck size={16} />
              <span>默认由 SAM3 完成关键帧文本分割与双向视频跟踪；SAM2 组合可作为对照。VOID 负责后续视频补洞。每次只运行一个 GPU 清理阶段。</span>
            </div>
            {catalog.data?.preview_available && propagationModel === "sam2" && requirement("sam2_checkpoint")?.available === false && (
              <p className="notice warning cleanup-warning">所选 SimFoundry 原组合需要本机 SAM2 checkpoint；当前未检测到，切回 SAM3 单模型或安装该权重。</p>
            )}
            {catalog.data?.preview_available && useGemini && requirement("gemini")?.available === false && (
              <p className="notice warning cleanup-warning">Gemini 对象提案需要配置 GCP 项目；也可以关闭该选项并填写对象提示词。</p>
            )}
            <button className="button primary" disabled={create.isPending || !canStart}>
              <Play size={15} />
              {create.isPending ? "正在启动…" : "生成 mask 预览"}
            </button>
          </form>
          {catalog.data && (
            <div className="cleanup-requirements">
              <div className="section-heading"><h3>运行环境</h3><span className={catalog.data.preview_available ? "state-good" : "state-bad"}>{catalog.data.preview_available ? "可生成 mask" : "尚不可运行"}</span></div>
              <div className="requirement-list">
                {catalog.data.requirements.filter((item) =>
                  !["da3_env", "nerfstudio_env", "3dgrut_env", "mesh_env"].includes(item.id) &&
                  (item.id !== "sam2_checkpoint" || propagationModel === "sam2"),
                ).map((item) => (
                  <div className="requirement-item" key={item.id}>
                    <span className={item.available ? "requirement-dot good" : "requirement-dot bad"} />
                    <span><strong>{item.title}</strong><small>{item.detail}</small></span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        <div className="panel cleanup-results-panel">
          <div className="section-heading">
            <div><h2>任务与检查</h2><p className="muted small">对象 mask 和补洞结果都需要人工检查。</p></div>
            <span className="counter">{jobs.data?.length ?? 0}</span>
          </div>
          {jobs.isLoading ? <Loading /> : jobs.data?.length ? (
            <div className="cleanup-job-list">
              {jobs.data.map((job) => (
                <button key={job.id} className={`cleanup-job-row ${selectedJob?.id === job.id ? "selected" : ""}`} onClick={() => setSelectedJobId(job.id)}>
                  <span className={`cleanup-job-mark ${job.status}`} />
                  <span><strong>{job.source_video.name}</strong><small>{statusNames[job.status]} · {job.scene_name}</small></span>
                  <small>{job.propagation_model.toUpperCase()}</small>
                </button>
              ))}
            </div>
          ) : <div className="cleanup-empty">尚无清理任务。先从左侧视频开始生成 mask 预览。</div>}

          {selectedJob && <CleanupReview job={selectedJob} onApprove={() => approve.mutate(selectedJob.id)}
            onBuild={() => build.mutate(selectedJob.id)} onStop={() => stop.mutate(selectedJob.id)}
            busy={approve.isPending || build.isPending || stop.isPending} canBuild={catalog.data?.build_available ?? false} />}
        </div>
      </section>
    </main>
  );
}

function CleanupReview({ job, onApprove, onBuild, onStop, busy, canBuild }: {
  job: CleanupJob;
  onApprove: () => void;
  onBuild: () => void;
  onStop: () => void;
  busy: boolean;
  canBuild: boolean;
}) {
  return (
    <div className="cleanup-review">
      <div className="cleanup-review-header">
        <div><span className={`cleanup-status ${job.status}`}>{statusNames[job.status]}</span><p>{job.current_step}</p><small className="muted">{job.message}</small></div>
        {isActive(job) && <button className="button" onClick={onStop} disabled={busy}><Square size={14} />停止</button>}
      </div>
      {job.status === "review_mask" && (
        <>
          <div className="notice warning cleanup-warning"><AlertTriangle size={16} /><span>确认彩色区域只覆盖要移除的对象，并在黑白视频中检查跨帧跟踪。mask 不正确时请调整对象提示词后重新生成。</span></div>
          {job.detected_objects.length > 0 && <div className="cleanup-object-tags">{job.detected_objects.map((name) => <span key={name}>{name}</span>)}</div>}
          <div className="cleanup-preview-grid mask-review-grid">
            {job.mask_preview_url && <figure><img src={job.mask_preview_url} alt="SAM 关键帧对象分割 overlay" /><figcaption>关键帧分割 overlay</figcaption></figure>}
            {job.mask_video_url && <figure><video controls src={job.mask_video_url} /><figcaption>跨帧目标 mask（黑色为待移除区域）</figcaption></figure>}
          </div>
          <div className="cleanup-actions">
            <button className="button primary" onClick={onApprove} disabled={busy}><Check size={15} />确认 mask，运行 VOID 补洞</button>
            <a className="button" href={job.log_url} target="_blank" rel="noreferrer">查看运行日志</a>
          </div>
        </>
      )}
      {job.status === "inpainting" && <video className="cleanup-source-player" controls src={job.source_video_url} />}
      {job.status === "review_clean" && (
        <>
          <div className="notice warning cleanup-warning"><AlertTriangle size={16} /><span>检查机器人和对象是否移除、背景是否自然、是否出现漂浮/闪烁伪影。补洞结果需要人工确认。</span></div>
          <div className="cleanup-preview-grid">
            <figure><video controls src={job.source_video_url} /><figcaption>输入视频</figcaption></figure>
            {job.cleaned_video_url && <figure><video controls src={job.cleaned_video_url} /><figcaption>VOID Pass 2 清理结果</figcaption></figure>}
          </div>
          {job.can_build_scene ? (
            <div className="cleanup-actions">
              <button className="button primary" onClick={onBuild} disabled={busy || !canBuild}><Check size={15} />确认结果，构建 3D 场景</button>
              {!canBuild && <small className="muted">完整场景环境未就绪，查看左侧运行环境说明。</small>}
            </div>
          ) : <div className="notice cleanup-note">该输入是操作演示，只验证 mask 和视频补洞，不进入相机扫描的 3D 重建。</div>}
        </>
      )}
      {job.status === "building" && job.cleaned_video_url && <video className="cleanup-source-player" controls src={job.cleaned_video_url} />}
      {job.status === "ready" && <div className="notice success cleanup-note">场景已生成：<span className="mono">{job.scene_path}</span></div>}
      {job.status === "failed" && <div className="notice danger cleanup-note">阶段失败，已保留日志和中间产物。<a href={job.log_url} target="_blank" rel="noreferrer">打开日志</a></div>}
      {job.status === "stopped" && <div className="notice cleanup-note">任务已停止，已保留日志和中间产物。</div>}
    </div>
  );
}
