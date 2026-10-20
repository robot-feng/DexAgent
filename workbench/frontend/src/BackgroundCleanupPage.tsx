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
  const [proposalBackend, setProposalBackend] = useState<"codex_external" | "gemini" | "manual">("codex_external");
  const [propagationModel, setPropagationModel] = useState<"sam3" | "sam2" | "litetext">("litetext");
  const [selectedJobId, setSelectedJobId] = useState("");

  const selectedVideo = useMemo(
    () => catalog.data?.videos.find((video) => video.id === videoId),
    [catalog.data?.videos, videoId],
  );
  const selectedJob = jobs.data?.find((job) => job.id === selectedJobId) ?? jobs.data?.[0];
  const requirement = (id: string) => catalog.data?.requirements.find((item) => item.id === id);
  const propagationReady = propagationModel === "litetext"
    ? requirement("litetext_runtime")?.available
    : requirement("simfoundry_sam3")?.available &&
      (propagationModel === "sam3" || requirement("sam2_checkpoint")?.available);
  const canStart = Boolean(
    catalog.data?.preview_available &&
    propagationReady &&
    (proposalBackend !== "gemini" || requirement("gemini")?.available) &&
    videoId &&
    (proposalBackend !== "manual" || objectNames(includeText).length),
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
      proposal_backend: proposalBackend,
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
              <span>{proposalBackend === "manual" ? "要移除的对象提示词" : "补充要移除的对象（可选）"}</span>
              <textarea aria-label="要移除的对象提示词" value={includeText} onChange={(event) => setIncludeText(event.target.value)} placeholder="例如：robot arm, gripper" rows={3} />
              <small className="muted">逗号分隔。固定机器人、夹爪或人手容易漏检，可把它们作为明确的补充移除对象。</small>
            </label>
            <label>
              <span>保留对象（可选）</span>
              <input aria-label="保留对象" value={skipText} onChange={(event) => setSkipText(event.target.value)} placeholder="逗号分隔" />
            </label>
            <div className="cleanup-model-row">
              <label>
                <span>视频分割模型</span>
                <select aria-label="视频分割模型" value={propagationModel} onChange={(event) => setPropagationModel(event.target.value as typeof propagationModel)}>
                  <option value="litetext">SAM3 LiteText 视频跟踪（本机 S0 权重）</option>
                  <option value="sam3">SAM3 单模型（文字分割 + 跨帧跟踪）</option>
                  <option value="sam2">SimFoundry 原流程（SAM3 mask + SAM2 传播）</option>
                </select>
              </label>
              <label>
                <span>对象提案 provider</span>
                <select aria-label="对象提案 provider" value={proposalBackend} onChange={(event) => setProposalBackend(event.target.value as typeof proposalBackend)}>
                  <option value="codex_external">DexAgent / Codex external（推荐）</option>
                  <option value="gemini">Gemini API / Vertex（上游兼容）</option>
                  <option value="manual">手动对象提示词</option>
                </select>
              </label>
            </div>
            <div className="notice cleanup-note">
              <ShieldCheck size={16} />
              <span>LiteText 仅替换 SAM3 文本编码器，视频仍由 SAM3 原生双向跟踪器传播；目标在主关键帧不可见时，会自动尝试其它抽样帧播种。确认整段彩色 mask 没有漏检或漂移后再运行 VOID 补洞。</span>
            </div>
            {catalog.data?.preview_available && propagationModel === "sam2" && requirement("sam2_checkpoint")?.available === false && (
              <p className="notice warning cleanup-warning">所选 SimFoundry 原组合需要本机 SAM2 checkpoint；当前未检测到，切回 SAM3 单模型或安装该权重。</p>
            )}
            {catalog.data?.preview_available && proposalBackend === "gemini" && requirement("gemini")?.available === false && (
              <p className="notice warning cleanup-warning">所选 Gemini provider 需要 Gemini API key 或 Vertex 项目；也可以切换到 DexAgent external 或手动提示词。</p>
            )}
            {proposalBackend === "codex_external" && (
              <p className="muted small">Agent 请求与图像会保存在本次任务目录；任务启动后可从详情页复制 external transport 命令。</p>
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
                  (item.id !== "sam2_checkpoint" || propagationModel === "sam2") &&
                  (item.id !== "simfoundry_sam3" || propagationModel !== "litetext"),
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
      {job.status === "preparing" && job.proposal_backend === "codex_external" && job.proposal_transport_dir && (
        <div className="notice cleanup-note">
          <div><strong>等待 Agent 对象提案</strong><p>在终端读取本任务请求；拿到 request.json 后用 external inspect 查看关键帧，再提交 propose_objects 响应。</p>
            <code className="mono">python -m agent.external wait "{job.proposal_transport_dir}" --timeout 60</code>
            <small className="muted">重复 wait 直到返回 waiting 请求；响应示例和提交命令见工作台 README 的“背景清理 Agent 提案”。</small>
          </div>
        </div>
      )}
      {job.status === "review_mask" && (
        <>
          <div className="notice warning cleanup-warning"><AlertTriangle size={16} /><span>确认彩色区域只覆盖要移除的对象，并检查黑白视频中的运动、遮挡和自动选取的种子帧。mask 不正确时请调整对象提示词后重新生成。</span></div>
          {job.detected_objects.length > 0 && <div className="cleanup-object-tags">{job.detected_objects.map((name) => <span key={name}>{name}</span>)}</div>}
          <div className="cleanup-preview-grid mask-review-grid">
            {job.mask_preview_url && <figure><img src={job.mask_preview_url} alt="SAM 关键帧对象分割 overlay" /><figcaption>关键帧分割 overlay</figcaption></figure>}
            {job.mask_video_url && <figure><video controls src={job.mask_video_url} /><figcaption>视频目标 mask（黑色为待移除区域）</figcaption></figure>}
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
