import { useEffect, useRef, useState } from "react";
import { useBlocker, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Box, Maximize2, Square } from "lucide-react";
import { api, prefix, type SceneRevision } from "./api";
import { ErrorNotice, Loading, formatTime } from "./components";

type Reply = {
  resolve: (ok: boolean) => void;
  reject: (error: Error) => void;
  timer: ReturnType<typeof setTimeout>;
};

type LayerMode = "all" | "background" | "entities";

const layerModeLabels: Record<LayerMode, string> = {
  all: "背景、机器人与物体",
  background: "仅背景",
  entities: "仅机器人与物体",
};

export default function SceneEditor({
  available,
  reason,
}: {
  available: boolean;
  reason: string;
}) {
  const [params, setParams] = useSearchParams();
  const client = useQueryClient();
  const frame = useRef<HTMLIFrameElement>(null);
  const replies = useRef(new Map<string, Reply>());
  const sequence = useRef(0);
  const handlingNavigation = useRef(false);
  const [navigationError, setNavigationError] = useState<Error | null>(null);
  const [revisionOffset, setRevisionOffset] = useState(0);
  const [layerMode, setLayerMode] = useState<LayerMode>("all");
  const scenes = useQuery({ queryKey: ["scenes"], queryFn: api.scenes });
  const sessions = useQuery({
    queryKey: ["editors"],
    queryFn: api.editors,
    refetchInterval: 5000,
  });
  const active = sessions.data?.find((s) =>
    ["starting", "ready", "unresponsive"].includes(s.status),
  );
  const editorId =
    params.get("editor") ||
    (!params.get("scene") || params.get("scene") === active?.scene_id
      ? active?.id
      : undefined);
  const editor = useQuery({
    queryKey: ["editor", editorId],
    queryFn: () => api.editor(editorId!),
    enabled: !!editorId,
    refetchInterval: (query) =>
      query.state.data?.status === "starting" ? 1000 : 5000,
  });
  const sceneId =
    params.get("scene") || editor.data?.scene_id || scenes.data?.items[0]?.id;
  const scene = scenes.data?.items.find((s) => s.id === sceneId);
  const history = useQuery({
    queryKey: ["scene-revisions", sceneId, revisionOffset],
    queryFn: () => api.sceneRevisions(sceneId!, revisionOffset),
    enabled: !!sceneId,
    refetchInterval: 5000,
  });
  const state = editor.data;
  const bound = state?.scene_id === sceneId;
  const live = bound && state !== undefined && (state.status === "ready" || state.status === "unresponsive");
  const sessionPath = live ? state.url : null;
  const requestEditor = (action: "leave" | "view", mode?: "all" | "background" | "entities") =>
    new Promise<boolean>((resolve, reject) => {
      const target = frame.current?.contentWindow;
      if (!target || !sessionPath) {
        reject(new Error("编辑器尚未就绪，无法确认未保存修改。"));
        return;
      }
      // History actions can be below the iframe. Keep the native response visible
      // and clear of the fixed workbench header before asking it to open.
      frame.current?.scrollIntoView({ block: "center", behavior: "instant" });
      const id = String(++sequence.current);
      const timer = setTimeout(() => {
        replies.current.delete(id);
        reject(
          new Error(
            action === "leave"
              ? "编辑器未响应离开检查；可以留在当前页重试，或明确放弃浏览器中的修改。"
              : "编辑器未响应分层显示请求；请等待场景加载完成后重试。",
          ),
        );
      }, 5000);
      replies.current.set(id, { resolve, reject, timer });
      target.postMessage(
        {
          type: "robotsim-editor-request",
          session: sessionPath,
          request_id: id,
          action,
          mode,
        },
        window.location.origin,
      );
    });
  const requestLeave = () => requestEditor("leave");
  const view = useMutation({
    mutationFn: (mode: "all" | "background" | "entities") => requestEditor("view", mode),
    onSuccess: (ok, mode) => {
      if (ok) setLayerMode(mode);
    },
  });
  useEffect(() => setLayerMode("all"), [sessionPath]);
  useEffect(() => {
    const pending = replies.current;
    const receive = (event: MessageEvent) => {
      const message = event.data;
      if (
        event.origin !== window.location.origin ||
        event.source !== frame.current?.contentWindow ||
        message?.type !== "robotsim-editor-response" ||
        message.session !== sessionPath
      )
        return;
      const callback = pending.get(message.request_id);
      if (!callback) return;
      clearTimeout(callback.timer);
      if (message.pending) return;
      pending.delete(message.request_id);
      message.error
        ? callback.reject(new Error(message.error))
        : callback.resolve(message.ok === true);
    };
    window.addEventListener("message", receive);
    return () => {
      window.removeEventListener("message", receive);
      for (const callback of pending.values()) {
        clearTimeout(callback.timer);
        callback.resolve(false);
      }
      pending.clear();
    };
  }, [sessionPath]);
  const blocker = useBlocker(({ nextLocation }) => {
    if (!live) return false;
    if (nextLocation.pathname !== "/scenes") return true;
    const next = new URLSearchParams(nextLocation.search);
    return !!(
      (next.get("scene") && next.get("scene") !== sceneId) ||
      (next.get("editor") && next.get("editor") !== editorId)
    );
  });
  useEffect(() => {
    if (blocker.state !== "blocked" || handlingNavigation.current) return;
    handlingNavigation.current = true;
    setNavigationError(null);
    requestLeave()
      .then(async (ok) => {
        if (!ok) {
          blocker.reset();
          return;
        }
        const next = new URLSearchParams(blocker.location.search).get("scene");
        if (
          blocker.location.pathname === "/scenes" &&
          next &&
          next !== sceneId &&
          editorId
        ) {
          await api.stopEditor(editorId);
          await client.invalidateQueries({ queryKey: ["editors"] });
        }
        blocker.proceed();
      })
      .catch((error) => setNavigationError(error))
      .finally(() => {
        handlingNavigation.current = false;
      });
  }, [blocker.state]);
  const create = useMutation({
    mutationFn: async (revision?: SceneRevision) => {
      if (live && !(await requestLeave())) return null;
      if (bound && state && ["starting", "ready", "unresponsive"].includes(state.status)) {
        const stopped = await api.stopEditor(state.id);
        client.setQueryData(["editor", state.id], stopped);
      }
      return api.createEditor(sceneId!, revision);
    },
    onSuccess: (state) => {
      if (!state) return;
      client.setQueryData(["editor", state.id], state);
      client.invalidateQueries({ queryKey: ["editors"] });
      const next = new URLSearchParams(params);
      next.set("scene", state.scene_id);
      next.set("editor", state.id);
      setParams(next);
    },
  });
  const stop = useMutation({
    mutationFn: async () => {
      if (live && !(await requestLeave())) return null;
      return api.stopEditor(editorId!);
    },
    onSuccess: (state) => {
      if (state) client.setQueryData(["editor", state.id], state);
      client.invalidateQueries({ queryKey: ["editors"] });
    },
  });

  return (
    <section className="panel native-editor">
      <div className="section-heading">
        <div>
          <p className="eyebrow">SCENE WORKSPACE</p>
          <h2>场景布局编辑</h2>
        </div>
        <div className="actions">
          {live && (
            <button
              className="button"
              onClick={() => frame.current?.requestFullscreen()}
            >
              <Maximize2 size={14} />
              全屏编辑
            </button>
          )}
          {bound &&
          ["starting", "ready", "unresponsive"].includes(
            state?.status || "",
          ) ? (
            <button
              className="button"
              disabled={stop.isPending || create.isPending}
              onClick={() => stop.mutate()}
            >
              <Square size={13} />
              停止会话
            </button>
          ) : (
            <button
              className="button primary"
              disabled={
                !available || !scene || !!scene.issue || create.isPending
              }
              onClick={() => create.mutate()}
            >
              <Box size={14} />
              打开原场景副本
            </button>
          )}
        </div>
      </div>
      <ErrorNotice
        error={
          scenes.error ??
          sessions.error ??
          editor.error ??
          create.error ??
          stop.error ??
          view.error ??
          history.error
        }
      />
      {!available && <p className="notice">{reason}</p>}
      {scenes.isLoading ? (
        <Loading />
      ) : scenes.data?.total === 0 ? (
        <p className="muted">
          当前没有可编辑的原生 scene JSON。可用 --scene-root 配置已有 SimFoundry
          场景目录。
        </p>
      ) : (
        <label className="editor-picker">
          编辑对象{" "}
          <select
            aria-label="选择可编辑场景"
            value={sceneId || ""}
            onChange={(event) => {
              setRevisionOffset(0);
              const next = new URLSearchParams(params);
              next.set("scene", event.target.value);
              next.delete("editor");
              setParams(next);
            }}
          >
            {scenes.data?.items.map((scene) => (
              <option value={scene.id} key={scene.id}>
                {scene.name}
                {scene.issue ? " · 读取异常" : ` · ${scene.objects} 个对象`}
              </option>
            ))}
          </select>
        </label>
      )}
      {scene && (
        <details className="scene-identity">
          <summary>场景来源与版本</summary>
          <code>{scene.path}</code>
          <span>来源 SHA-256：{scene.sha256 || "不可读取"}</span>
        </details>
      )}
      {scene?.issue && (
        <div className="notice danger" role="alert">
          {scene.issue}
        </div>
      )}
      {state && !bound && (
        <div className="notice danger" role="alert">
          此编辑会话属于其他场景，已阻止展示。
        </div>
      )}
      {bound && state?.status === "starting" && (
        <>
          <Loading />
          <p className="muted">{state.message}</p>
        </>
      )}
      {bound && state?.source_changed && (
        <div className="notice">
          原场景已变化；当前仍是打开时的副本。请保存候选，再停止会话并打开当前版本。
        </div>
      )}
      {bound && state?.parent_revision && (
        <p className="notice">
          当前从已保存候选继续编辑。基准候选 SHA-256：<code>{state.parent_revision.sha256}</code>
        </p>
      )}
      {bound && state?.status === "unresponsive" && (
        <div className="notice" role="status">
          {state.message}
        </div>
      )}
      {bound && state?.status === "failed" && (
        <pre className="notice danger" role="alert">
          {state.message}
        </pre>
      )}
      {bound && state?.status === "stopped" && (
        <p className="muted">{state.message}</p>
      )}
      {live && state.url && (
        <>
          <div className="scene-layer-tools">
            <strong className="small">分层检查</strong>
            <div className="actions" role="group" aria-label="场景分层显示">
              {(
                [
                  ["background", "仅看背景"],
                  ["entities", "仅看机器人与物体"],
                  ["all", "显示全部"],
                ] as const
              ).map(([mode, title]) => (
                <button
                  key={mode}
                  className="button"
                  aria-pressed={layerMode === mode}
                  disabled={view.isPending || create.isPending || stop.isPending}
                  onClick={() => view.mutate(mode)}
                >
                  {title}
                </button>
              ))}
            </div>
            <p className="small muted" aria-live="polite">
              当前显示：<strong>{layerModeLabels[layerMode]}</strong>。这里只切换可见性；扫描网格里已融合的实体不会被移除，背景清理和碰撞验证需另行完成。
            </p>
          </div>
          <iframe
            key={state.id}
            ref={frame}
            src={state.url}
            className="editor-frame"
            title="原生场景布局编辑器"
            sandbox="allow-scripts allow-same-origin allow-downloads allow-modals"
            allowFullScreen
          />
        </>
      )}
      {navigationError && blocker.state === "blocked" && (
        <div className="notice danger" role="alert">
          {navigationError.message}
          <div className="actions">
            <button
              className="button"
              onClick={() => {
                setNavigationError(null);
                blocker.reset();
              }}
            >
              留在当前页
            </button>
            <button
              className="button"
              onClick={() => {
                setNavigationError(null);
                blocker.proceed();
              }}
            >
              放弃修改并离开
            </button>
          </div>
        </div>
      )}
      <p className="small muted">
        布局编辑使用原生 Light
        Editor，不执行物理。保存产生新的候选文件，原场景和 USD
        资产保留；物理验收与版本发布另行执行。
      </p>
      {bound && state && (
        <a
          className="link-button small"
          href={`${prefix}/editors/${state.id}/log`}
          download
        >
          下载编辑器启动日志
        </a>
      )}
      {sceneId && (
        <div className="editor-revisions">
          <h3>已保存的候选版本</h3>
          <p className="small muted">包含该场景的历史会话；继续编辑会打开新副本，并保留版本来源。</p>
          {history.isLoading && <Loading />}
          {history.data?.total === 0 && <p className="muted">尚未保存候选。在编辑器中保存后，可在这里继续编辑或下载。</p>}
          {history.data?.items.map((revision) => (
            <div className="revision-row" key={revision.id}>
              <span>
                {revision.name}
                <small>
                  {formatTime(revision.modified)} · SHA-256 {revision.sha256}
                </small>
                <small>
                  {revision.parent_revision ? `继承候选 ${revision.parent_revision.sha256.slice(0, 12)}` : "基于原场景副本"}
                  {!scene || scene.issue ? " · 原场景不可读取" : revision.source_sha256 !== scene.sha256 && " · 原场景已变化"}
                </small>
              </span>
              <div className="actions">
                <button
                  className="button"
                  disabled={!available || !scene || !!scene.issue || create.isPending || stop.isPending || (bound && state?.status === "starting")}
                  onClick={() => create.mutate(revision)}
                >继续编辑</button>
                <a href={revision.url} className="link-button" download>下载 JSON ↗</a>
              </div>
            </div>
          ))}
          {history.data && history.data.total > 10 && (
            <div className="actions">
              <button className="button" disabled={revisionOffset === 0} onClick={() => setRevisionOffset(Math.max(0, revisionOffset - 10))}>上一页候选</button>
              <span className="small muted">{revisionOffset + 1}–{Math.min(revisionOffset + 10, history.data.total)} / {history.data.total}</span>
              <button className="button" disabled={revisionOffset + 10 >= history.data.total} onClick={() => setRevisionOffset(revisionOffset + 10)}>下一页候选</button>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
