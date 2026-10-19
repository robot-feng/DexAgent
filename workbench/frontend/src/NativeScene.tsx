import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { Box, Maximize2, RefreshCw } from "lucide-react";
import { useRef } from "react";
import { api } from "./api";
import { ErrorNotice, Loading } from "./components";

export default function NativeScene({ runId }: { runId: string }) {
  const [params, setParams] = useSearchParams();
  const client = useQueryClient();
  const iframe = useRef<HTMLIFrameElement>(null);
  const previewId = params.get("preview");
  const create = useMutation({
    mutationFn: () => api.createPreview(runId),
    onSuccess: (state) => {
      client.setQueryData(["preview", state.id], state);
      const next = new URLSearchParams(params);
      next.set("preview", state.id);
      setParams(next);
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
  if (state && state.run_id !== runId)
    return (
      <section className="native-scene">
        <div className="notice danger" role="alert">
          此预览属于另一条运行，已阻止在当前场景中展示。
        </div>
        <ErrorNotice error={create.error} />
        <button
          className="button"
          disabled={create.isPending}
          onClick={() => create.mutate()}
        >
          为当前运行生成回放
        </button>
      </section>
    );
  return (
    <section className="native-scene">
      <div className="section-heading">
        <div>
          <p className="eyebrow">NATIVE RECORDED 3D</p>
          <h2>交互式场景回放</h2>
        </div>
        <div className="actions">
          {state?.status === "ready" && (
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
            disabled={create.isPending || state?.status === "generating"}
            onClick={() => create.mutate()}
          >
            {state?.status === "ready" ? (
              <RefreshCw size={14} />
            ) : (
              <Box size={14} />
            )}
            {state?.stale
              ? "确认当前版本"
              : state?.status === "ready"
                ? "加载已有回放"
                : "生成 3D 回放"}
          </button>
        </div>
      </div>
      <ErrorNotice error={create.error ?? preview.error} />
      {state?.stale && (
        <div className="notice" role="status">
          源记录版本有变化或尚未比对；当前展示既有快照。点击“确认当前版本”检查并加载对应回放。
        </div>
      )}
      {(create.isPending || state?.status === "generating") && (
        <>
          <Loading />
          <p className="small muted">
            {state?.message || "正在检查原生场景、模型身份和记录状态…"}
          </p>
        </>
      )}
      {state?.status === "failed" && (
        <div className="notice danger" role="alert">
          {state.message}
        </div>
      )}
      {state?.status === "ready" && state.url && (
        <>
          <iframe
            ref={iframe}
            className="native-frame"
            src={state.url}
            sandbox="allow-scripts allow-downloads"
            allowFullScreen
            title="原生 3D 记录回放"
          />
          <p className="small muted">
            原始 {state.source_frames} 帧 / 预览 {state.preview_frames} 帧 ·{" "}
            {state.duration_s?.toFixed(2)} s · 模型身份已核对 · 未执行物理。
          </p>
          <p className="small muted">
            {state.state_source === "recorded_qpos_with_forward_kinematics"
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
