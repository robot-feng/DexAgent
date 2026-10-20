import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { Box, Maximize2, RefreshCw } from "lucide-react";
import { useRef } from "react";
import { api } from "./api";
import { ErrorNotice, Loading } from "./components";

export default function NativeScene({
  runId,
  trialReportId,
}: {
  runId: string;
  trialReportId?: string;
}) {
  const [params, setParams] = useSearchParams();
  const client = useQueryClient();
  const iframe = useRef<HTMLIFrameElement>(null);
  const diagnostic = trialReportId !== undefined;
  const previewParam = diagnostic ? "trial_preview" : "preview";
  const previewId = params.get(previewParam);
  const create = useMutation({
    mutationFn: () => api.createPreview(runId, trialReportId),
    onSuccess: (state) => {
      client.setQueryData(["preview", state.id], state);
      setParams((current) => {
        if (diagnostic && current.get("trial") !== trialReportId)
          return current;
        const next = new URLSearchParams(current);
        next.set(previewParam, state.id);
        return next;
      });
    },
  });
  const preview = useQuery({
    queryKey: ["preview", previewId],
    queryFn: () => api.preview(previewId!),
    enabled: !!previewId,
    refetchInterval: (query) =>
      query.state.data?.status === "generating" ? 1000 : 5_000,
  });
  const state = preview.data;
  const stateMatchesContext =
    state?.run_id === runId &&
    state.trial_report_id === (trialReportId ?? null);
  const visibleState = stateMatchesContext ? state : undefined;
  if (state && !stateMatchesContext)
    return (
      <section className="native-scene">
        <div className="notice danger" role="alert">
          {diagnostic
            ? "此回放与当前所选规划试验不匹配，已阻止展示。"
            : "此预览属于另一条运行或规划试验，已阻止在当前场景中展示。"}
        </div>
        <ErrorNotice error={create.error} />
        <button
          className="button"
          disabled={create.isPending}
          onClick={() => create.mutate()}
        >
          {diagnostic ? "为当前试验生成回放" : "为当前运行生成回放"}
        </button>
      </section>
    );
  return (
    <section className="native-scene">
      <div className="section-heading">
        <div>
          <p className="eyebrow">
            {diagnostic
              ? "PLANNING TRIAL / NATIVE RECORDED 3D"
              : "NATIVE RECORDED 3D"}
          </p>
          <h2>{diagnostic ? "试验实际记录回放" : "交互式场景回放"}</h2>
        </div>
        <div className="actions">
          {visibleState?.status === "ready" && (
            <button
              className="button"
              aria-label="全屏 3D 回放"
              onClick={() => iframe.current?.requestFullscreen()}
            >
              <Maximize2 size={14} />
              全屏
            </button>
          )}
          <button
            className="button primary"
            disabled={create.isPending || visibleState?.status === "generating"}
            onClick={() => create.mutate()}
          >
            {visibleState?.status === "ready" ? (
              <RefreshCw size={14} />
            ) : (
              <Box size={14} />
            )}
            {visibleState?.stale
              ? "确认当前版本"
              : visibleState?.status === "ready"
                ? diagnostic
                  ? "重新检查试验记录"
                  : "加载已有回放"
                : diagnostic
                  ? "生成试验记录回放"
                  : "生成 3D 回放"}
          </button>
        </div>
      </div>
      <ErrorNotice error={create.error ?? preview.error} />
      {diagnostic && (
        <div className="notice" role="note">
          回放展示所选规划试验的实际记录，不代表任务成功或合格执行记录；查看过程不会重新运行物理。
        </div>
      )}
      {visibleState?.stale && (
        <div className="notice" role="status">
          源记录版本有变化或尚未比对；当前展示既有快照。点击“确认当前版本”检查并加载对应回放。
        </div>
      )}
      {(create.isPending || visibleState?.status === "generating") && (
        <>
          <Loading />
          <p className="small muted">
            {visibleState?.message || "正在检查原生场景、模型身份和记录状态…"}
          </p>
        </>
      )}
      {visibleState?.status === "failed" && (
        <div className="notice danger" role="alert">
          {visibleState.message}
        </div>
      )}
      {visibleState?.status === "ready" && visibleState.url && (
        <>
          <iframe
            ref={iframe}
            className="native-frame"
            src={visibleState.url}
            sandbox="allow-scripts allow-downloads"
            allowFullScreen
            title={diagnostic ? "规划试验实际记录回放" : "原生 3D 记录回放"}
          />
          <p className="small muted">
            原始 {visibleState.source_frames} 帧 / 预览{" "}
            {visibleState.preview_frames} 帧 ·{" "}
            {visibleState.duration_s?.toFixed(2)} s · 模型身份已核对 ·
            未执行物理。
          </p>
          <p className="small muted">
            {diagnostic
              ? "来源：所选规划试验的实际记录轨迹。"
              : visibleState.state_source ===
                  "recorded_qpos_with_forward_kinematics"
                ? "来源：DexAgent 已记录关节姿态，经正向运动学显示。"
                : "来源：RSS 已记录刚体位姿。"}
          </p>
        </>
      )}
      <p className="small muted">
        使用原生 Viser
        查看记录中的刚体状态，可旋转、缩放、播放和拖动时间轴。该视图用于回放，不编辑场景或重新计算物理。
      </p>
    </section>
  );
}
